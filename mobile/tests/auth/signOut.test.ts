import { confirmSignOut, signOutPrompt } from '../../src/features/auth/signOut';
import { AuthStore } from '../../src/auth/authStore';
import { configureApiClient, resetApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import {
  AUTH_PENDING_REVOKE_KEY,
  createMemorySecureStoreAdapter,
  CredentialStore,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
  secureGet,
} from '../../src/platform/secureStore';
import { resetPrivateCacheBackend } from '../../src/platform/privateCache';
import { createFixtureFetch } from '../../src/test/server';

describe('signOut helper', () => {
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
    resetPrivateCacheBackend();
    resetApiClient();
    AuthStore.replaceQueryClient();
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async () => ({ status: 204 })),
    });
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetPrivateCacheBackend();
    resetApiClient();
  });

  it('prompts for staged uploads before discarding work', () => {
    expect(signOutPrompt(true)).toEqual({ kind: 'staged_upload' });
    expect(signOutPrompt(false)).toEqual({ kind: 'confirm' });
  });

  it('completes online 204 sign-out and clears pending revoke', async () => {
    await AuthStore.completeExchange('ciat_z', { kind: 'ordinary' });
    const result = await confirmSignOut({
      hasStagedUpload: false,
      discardLocalWork: true,
    });
    expect(result).toBe('signed_out');
    expect(await CredentialStore.get()).toBeNull();
    expect(await secureGet(AUTH_PENDING_REVOKE_KEY)).toBeNull();
  });

  it('keeps pending revoke on 202 drain', async () => {
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async () => ({
        status: 202,
        body: {
          resource_version: 1,
          status: 'pending',
          retry_after_seconds: 1,
        },
      })),
    });
    await AuthStore.completeExchange('ciat_pending', { kind: 'ordinary' });
    const result = await confirmSignOut({
      hasStagedUpload: false,
      discardLocalWork: true,
    });
    expect(result).toBe('pending_revoke');
    expect(await secureGet(AUTH_PENDING_REVOKE_KEY)).toContain('ciat_pending');
  });
});
