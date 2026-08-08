import {
  clearPendingAuth,
  exchangeEmailSignIn,
  readPendingAuth,
  savePendingAuth,
  startEmailSignIn,
} from '../../src/features/auth/api';
import { configureApiClient, resetApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import {
  createMemorySecureStoreAdapter,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
} from '../../src/platform/secureStore';
import { createFixtureFetch, headerMapLower } from '../../src/test/server';
import type { NativeAuthExchangeResult } from '../../src/features/auth/api';

describe('email auth API', () => {
  const env = resolveAppEnvironment({
    appEnv: 'development',
    apiBaseUrl: 'https://api.example.com',
    env: {},
  });
  const identity = deriveAppIdentityHeaders({
    environment: env,
    platform: 'ios',
    appVersion: '1.0.0',
    appBuild: '1',
    applicationId: 'com.caddieinsight.app.dev',
  });

  beforeEach(() => {
    setSecureStoreAdapter(createMemorySecureStoreAdapter());
    resetApiClient();
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetApiClient();
  });

  it('starts email sign-in without Authorization and stores pending PKCE locally', async () => {
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async (req) => {
        const headers = headerMapLower(req.headers);
        expect(headers.authorization).toBeUndefined();
        const body = JSON.parse(String(req.body));
        expect(body.email).toBe('golfer@example.com');
        return {
          status: 202,
          body: {
            resource_version: 1,
            challenge_id: 'chal-1',
            expires_at: 1,
          },
        };
      }),
    });

    const started = await startEmailSignIn({
      email: 'golfer@example.com',
      deviceLabel: 'iPhone',
      installationId: '11111111-1111-4111-8111-111111111111',
      challenge: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    });
    expect(started.challenge_id).toBe('chal-1');
    await savePendingAuth({
      challengeId: started.challenge_id,
      verifier: 'verifier-value-with-enough-length-to-pass-43ch',
      idempotencyKey: '0123456789abcdef0123456789abcdef',
      kind: 'ordinary',
      startedAt: Date.now(),
    });
    const pending = await readPendingAuth();
    expect(pending?.challengeId).toBe('chal-1');
    expect(JSON.stringify(pending)).not.toContain('golfer@example.com');
  });

  it('narrows exchange success vs pending by generated status discriminator', async () => {
    const bodies: NativeAuthExchangeResult[] = [
      {
        resource_version: 1,
        status: 'authenticated',
        access_token: 'ciat_secret',
        expires_at: 9,
      },
      {
        resource_version: 1,
        status: 'pending',
        exchange_id: 'ex-1',
        retry_after_seconds: 1,
      },
    ];
    let i = 0;
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async () => ({
        status: bodies[i]!.status === 'authenticated' ? 201 : 202,
        body: bodies[i++],
      })),
    });

    const success = await exchangeEmailSignIn({
      challengeId: 'chal',
      emailCode: '12345678',
      verifier: 'verifier-value-with-enough-length-to-pass-43ch',
      idempotencyKey: '0123456789abcdef0123456789abcdef',
    });
    expect(success.result.status).toBe('authenticated');
    if (success.result.status === 'authenticated') {
      expect(success.result.access_token.startsWith('ciat_')).toBe(true);
    }

    const pending = await exchangeEmailSignIn({
      challengeId: 'chal',
      emailCode: '12345678',
      verifier: 'verifier-value-with-enough-length-to-pass-43ch',
      idempotencyKey: '0123456789abcdef0123456789abcdef',
    });
    expect(pending.result.status).toBe('pending');
    await clearPendingAuth();
  });
});
