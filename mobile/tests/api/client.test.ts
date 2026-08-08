import {
  apiRequest,
  configureApiClient,
  resetApiClient,
} from '../../src/api/client';
import { ApiRequestError } from '../../src/api/errors';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import {
  createMemorySecureStoreAdapter,
  CredentialStore,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
} from '../../src/platform/secureStore';
import { createFixtureFetch, headerMapLower } from '../../src/test/server';

describe('apiRequest transport', () => {
  const env = resolveAppEnvironment({
    appEnv: 'development',
    apiBaseUrl: 'https://api.example.com',
    env: {},
  });
  const identity = deriveAppIdentityHeaders({
    environment: env,
    platform: 'ios',
    appVersion: '1.0.0',
    appBuild: '7',
    applicationId: 'com.caddieinsight.app.dev',
  });

  let memory = createMemorySecureStoreAdapter();

  beforeEach(async () => {
    memory = createMemorySecureStoreAdapter();
    setSecureStoreAdapter(memory);
    resetApiClient();
    await CredentialStore.set('ciat_test.token');
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetApiClient();
  });

  function wire(fetchImpl: typeof fetch, onUnauthorized?: () => void) {
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl,
      getBearer: () => CredentialStore.get(),
      onUnauthorized,
    });
  }

  it('injects bearer, Accept, and exact identity headers without Origin', async () => {
    let seen: Record<string, string> = {};
    wire(
      createFixtureFetch(async (req) => {
        seen = headerMapLower(req.headers);
        return { status: 200, body: { resource_version: 1, ok: true } };
      }),
    );

    await apiRequest('/api/v1/me');
    expect(seen.authorization).toBe('Bearer ciat_test.token');
    expect(seen.accept).toBe('application/json');
    expect(seen['x-caddieinsight-environment']).toBe('development');
    expect(seen['x-caddieinsight-platform']).toBe('ios');
    expect(seen['x-caddieinsight-app-version']).toBe('1.0.0');
    expect(seen['x-caddieinsight-app-build']).toBe('7');
    expect(seen['x-caddieinsight-application-id']).toBe(
      'com.caddieinsight.app.dev',
    );
    expect(seen.origin).toBeUndefined();
  });

  it('does not log raw bearer tokens in error messages', async () => {
    wire(
      createFixtureFetch(async () => ({
        status: 500,
        body: { code: 'internal_error', message: 'boom', retryable: true },
      })),
    );
    await expect(apiRequest('/api/v1/me')).rejects.toThrow(/internal_error|500/);
    try {
      await apiRequest('/api/v1/me');
    } catch (error) {
      expect(String(error)).not.toContain('ciat_test.token');
    }
  });

  it('retries idempotent GET once on network failure', async () => {
    let calls = 0;
    wire(
      createFixtureFetch(async () => {
        calls += 1;
        if (calls === 1) {
          throw new TypeError('Network request failed');
        }
        return { status: 200, body: { resource_version: 1 } };
      }),
    );
    await expect(apiRequest('/api/v1/me')).resolves.toEqual({
      resource_version: 1,
    });
    expect(calls).toBe(2);
  });

  it('does not retry POST without an idempotency key', async () => {
    let calls = 0;
    wire(
      createFixtureFetch(async () => {
        calls += 1;
        throw new TypeError('Network request failed');
      }),
    );
    await expect(
      apiRequest('/api/v1/events', { method: 'POST', body: '{}' }),
    ).rejects.toBeInstanceOf(ApiRequestError);
    expect(calls).toBe(1);
  });

  it('replays the identical idempotency key on retry', async () => {
    const keys: Array<string | undefined> = [];
    let calls = 0;
    wire(
      createFixtureFetch(async (req) => {
        calls += 1;
        keys.push(headerMapLower(req.headers)['idempotency-key']);
        if (calls === 1) {
          throw new TypeError('Network request failed');
        }
        return { status: 204 };
      }),
    );
    await apiRequest('/api/v1/auth/sign-out', {
      method: 'POST',
      idempotencyKey: '0123456789abcdef0123456789abcdef',
    });
    expect(keys).toEqual([
      '0123456789abcdef0123456789abcdef',
      '0123456789abcdef0123456789abcdef',
    ]);
  });

  it('translates structured API errors including 401 WWW-Authenticate', async () => {
    const unauthorized = jest.fn();
    wire(
      createFixtureFetch(async () => ({
        status: 401,
        headers: { 'WWW-Authenticate': 'Bearer realm="ci"' },
        body: {
          code: 'authentication_rejected',
          message: 'nope',
          retryable: false,
          reference_id: 'ref-1',
        },
      })),
      unauthorized,
    );
    try {
      await apiRequest('/api/v1/me');
      throw new Error('expected throw');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiRequestError);
      const appError = (error as ApiRequestError).appError;
      expect(appError).toMatchObject({
        category: 'auth',
        apiCode: 'authentication_rejected',
        status: 401,
        referenceId: 'ref-1',
        authenticate: 'Bearer realm="ci"',
        retryable: false,
      });
    }
    expect(unauthorized).toHaveBeenCalled();
    expect(await CredentialStore.get()).toBe('ciat_test.token');
  });

  it('parses numeric and HTTP-date Retry-After on 429', async () => {
    wire(
      createFixtureFetch(async () => ({
        status: 429,
        headers: { 'Retry-After': '12' },
        body: { code: 'rate_limited', message: 'slow', retryable: true },
      })),
    );
    try {
      await apiRequest('/api/v1/me');
    } catch (error) {
      expect((error as ApiRequestError).appError.retryAfterSeconds).toBe(12);
      expect((error as ApiRequestError).appError.category).toBe('rate_limit');
    }

    const future = new Date(Date.now() + 5_000).toUTCString();
    wire(
      createFixtureFetch(async () => ({
        status: 429,
        headers: { 'Retry-After': future },
        body: { code: 'rate_limited', message: 'slow', retryable: true },
      })),
    );
    try {
      await apiRequest('/api/v1/me');
    } catch (error) {
      const seconds = (error as ApiRequestError).appError.retryAfterSeconds;
      expect(seconds).not.toBeNull();
      expect(seconds!).toBeGreaterThanOrEqual(0);
      expect(seconds!).toBeLessThanOrEqual(10);
    }
  });

  it('maps 409, 507, and upload restore status codes safely', async () => {
    const cases: Array<{
      status: number;
      body: Record<string, unknown>;
      category: string;
    }> = [
      {
        status: 409,
        body: { code: 'history_epoch_conflict', message: 'x', retryable: false },
        category: 'conflict',
      },
      {
        status: 507,
        body: { code: 'insufficient_storage', message: 'x', retryable: false },
        category: 'capacity',
      },
      {
        status: 409,
        body: {
          code: 'source_unavailable_after_restore',
          message: 'x',
          retryable: false,
        },
        category: 'conflict',
      },
    ];
    for (const item of cases) {
      wire(
        createFixtureFetch(async () => ({
          status: item.status,
          body: item.body,
        })),
      );
      try {
        await apiRequest('/api/v1/uploads/u1');
        throw new Error('expected throw');
      } catch (error) {
        const appError = (error as ApiRequestError).appError;
        expect(appError.category).toBe(item.category);
        expect(appError.apiCode).toBe(item.body.code);
        expect(appError.status).toBe(item.status);
      }
    }
  });

  it('rejects caller Origin and identity header overrides', async () => {
    wire(createFixtureFetch(async () => ({ status: 200, body: {} })));
    await expect(
      apiRequest('/api/v1/me', { headers: { Origin: 'https://evil.example' } }),
    ).rejects.toThrow(/Origin/);
    await expect(
      apiRequest('/api/v1/me', {
        headers: { 'X-CaddieInsight-Environment': 'production' },
      }),
    ).rejects.toThrow(/identity/);
  });

  it('translates malformed JSON bodies', async () => {
    const fetchImpl: typeof fetch = async () =>
      new Response('{not-json', {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    wire(fetchImpl);
    try {
      await apiRequest('/api/v1/me');
    } catch (error) {
      expect((error as ApiRequestError).appError.apiCode).toBe('malformed_json');
    }
  });
});
