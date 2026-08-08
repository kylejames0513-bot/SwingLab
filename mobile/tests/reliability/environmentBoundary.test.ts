import { resolveAppEnvironment } from '../../src/config/env';
import {
  EnvironmentBoundary,
  resetEnvironmentBoundaryForTests,
  setEnvironmentQueryClient,
} from '../../src/platform/environmentBoundary';
import { PrivateCache, resetPrivateCacheBackend } from '../../src/platform/privateCache';
import {
  AUTH_TOKEN_KEY,
  ENV_MARKER_KEY,
  ENV_PURGE_JOURNAL_KEY,
  INSTALLATION_ID_KEY,
  SECURE_STORE_PURGE_KEYS,
  createMemorySecureStoreAdapter,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
  secureGet,
  secureSet,
} from '../../src/platform/secureStore';
import { resetApiClient, configureApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { createFixtureFetch } from '../../src/test/server';

describe('EnvironmentBoundary', () => {
  let memory = createMemorySecureStoreAdapter();
  const queryClear = jest.fn();

  function envFor(origin: string, environment: 'development' | 'staging' | 'production') {
    return resolveAppEnvironment({
      appEnv: environment,
      apiBaseUrl: `${origin}`,
      env: {},
      buildProfile: environment,
      bundleIdentity:
        environment === 'production'
          ? 'com.caddieinsight.app'
          : environment === 'staging'
            ? 'com.caddieinsight.app.staging'
            : 'com.caddieinsight.app.dev',
    });
  }

  beforeEach(() => {
    memory = createMemorySecureStoreAdapter();
    setSecureStoreAdapter(memory);
    resetPrivateCacheBackend();
    resetEnvironmentBoundaryForTests();
    resetApiClient();
    queryClear.mockClear();
    setEnvironmentQueryClient({ clear: queryClear });
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetPrivateCacheBackend();
    resetEnvironmentBoundaryForTests();
    resetApiClient();
  });

  it('includes every durable SecureStore namespace in the purge inventory', () => {
    expect(SECURE_STORE_PURGE_KEYS).toEqual(
      expect.arrayContaining([
        'ci.auth.token.v1',
        'ci.auth.session.v1',
        'ci.auth.pending_revoke.v1',
        'ci.install.id.v1',
        'ci.auth.pkce.pending.v1',
      ]),
    );
  });

  it('preserves state on same-identity restart', async () => {
    const env = envFor('https://api.example.com', 'development');
    await EnvironmentBoundary.bootstrap(env);
    await secureSet(AUTH_TOKEN_KEY, 'ciat_keep');
    await secureSet(INSTALLATION_ID_KEY, '11111111-1111-1111-1111-111111111111');
    resetEnvironmentBoundaryForTests();
    await EnvironmentBoundary.bootstrap(env);
    expect(await secureGet(AUTH_TOKEN_KEY)).toBe('ciat_keep');
    expect(await secureGet(INSTALLATION_ID_KEY)).toBe(
      '11111111-1111-1111-1111-111111111111',
    );
    expect(EnvironmentBoundary.assertReady()).toBeUndefined();
  });

  it('purges when staging switches to production', async () => {
    const staging = envFor('https://staging.example.com', 'staging');
    await EnvironmentBoundary.bootstrap(staging);
    await secureSet(AUTH_TOKEN_KEY, 'ciat_staging');
    await PrivateCache.setActiveAccount('acct');
    await PrivateCache.writeJson('secret', { v: 1 });

    const production = envFor('https://api.caddieinsight.com', 'production');
    resetEnvironmentBoundaryForTests();
    await EnvironmentBoundary.bootstrap(production);

    expect(await secureGet(AUTH_TOKEN_KEY)).toBeNull();
    expect(await PrivateCache.readJson('secret')).toBeNull();
    expect(queryClear).toHaveBeenCalled();
    const marker = JSON.parse(String(await secureGet(ENV_MARKER_KEY)));
    expect(marker.environmentIdentity).toBe(production.environmentIdentity);
    expect(await secureGet(ENV_PURGE_JOURNAL_KEY)).toBeNull();
  });

  it('purges when the canonical origin changes in the same environment', async () => {
    const first = envFor('https://api-a.example.com', 'development');
    await EnvironmentBoundary.bootstrap(first);
    await secureSet(AUTH_TOKEN_KEY, 'ciat_a');

    const second = envFor('https://api-b.example.com', 'development');
    resetEnvironmentBoundaryForTests();
    await EnvironmentBoundary.bootstrap(second);
    expect(await secureGet(AUTH_TOKEN_KEY)).toBeNull();
  });

  it('treats a missing marker over existing state as untrusted and purges', async () => {
    await secureSet(AUTH_TOKEN_KEY, 'ciat_orphan');
    await secureSet(INSTALLATION_ID_KEY, '22222222-2222-2222-2222-222222222222');
    const env = envFor('https://api.example.com', 'development');
    await EnvironmentBoundary.bootstrap(env);
    expect(await secureGet(AUTH_TOKEN_KEY)).toBeNull();
    expect(await secureGet(INSTALLATION_ID_KEY)).toBeNull();
  });

  it('resumes an incomplete purge journal before allowing private work', async () => {
    const env = envFor('https://api.example.com', 'development');
    await secureSet(
      ENV_PURGE_JOURNAL_KEY,
      JSON.stringify({
        phase: 'started',
        targetIdentity: env.environmentIdentity,
        targetOrigin: env.apiOrigin,
      }),
    );
    await secureSet(AUTH_TOKEN_KEY, 'ciat_half');
    await EnvironmentBoundary.bootstrap(env);
    expect(await secureGet(AUTH_TOKEN_KEY)).toBeNull();
    expect(await secureGet(ENV_PURGE_JOURNAL_KEY)).toBeNull();
    expect(EnvironmentBoundary.assertReady()).toBeUndefined();
  });

  it('refuses API configuration until the gate is ready', async () => {
    expect(() => EnvironmentBoundary.assertReady()).toThrow(/not ready/);
    const env = envFor('https://api.example.com', 'development');
    const identity = deriveAppIdentityHeaders({
      environment: env,
      platform: 'ios',
      appVersion: '1.0.0',
      appBuild: '1',
      applicationId: 'com.caddieinsight.app.dev',
    });
    // Configuring before ready is a caller bug; gate still blocks assertReady.
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async () => ({ status: 200, body: {} })),
    });
    expect(() => EnvironmentBoundary.assertReady()).toThrow(/not ready/);
    await EnvironmentBoundary.bootstrap(env);
    EnvironmentBoundary.assertReady();
  });
});
