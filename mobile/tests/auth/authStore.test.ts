import { AuthStore } from '../../src/auth/authStore';
import { configureApiClient, resetApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import { PrivateCache, resetPrivateCacheBackend } from '../../src/platform/privateCache';
import {
  AUTH_PENDING_REVOKE_KEY,
  AUTH_SESSION_KEY,
  AUTH_TOKEN_KEY,
  createMemorySecureStoreAdapter,
  CredentialStore,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
  secureGet,
} from '../../src/platform/secureStore';
import { createFixtureFetch, headerMapLower } from '../../src/test/server';

describe('AuthStore', () => {
  const env = resolveAppEnvironment({
    appEnv: 'development',
    apiBaseUrl: 'https://api.example.com',
    env: {},
  });
  const identity = deriveAppIdentityHeaders({
    environment: env,
    platform: 'android',
    appVersion: '1.0.0',
    appBuild: '3',
    applicationId: 'com.caddieinsight.app.dev',
  });

  let memory = createMemorySecureStoreAdapter();

  beforeEach(() => {
    memory = createMemorySecureStoreAdapter();
    setSecureStoreAdapter(memory);
    resetPrivateCacheBackend();
    resetApiClient();
    AuthStore.replaceQueryClient();
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async () => ({ status: 204 })),
      getBearer: () => CredentialStore.get(),
    });
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetPrivateCacheBackend();
    resetApiClient();
  });

  it('bootstraps a signed-in session from SecureStore', async () => {
    await CredentialStore.set('ciat_live');
    await memory.setItemAsync(
      AUTH_SESSION_KEY,
      JSON.stringify({ kind: 'ordinary' }),
    );
    const state = await AuthStore.bootstrap();
    expect(state).toEqual({ status: 'signed_in', session: { kind: 'ordinary' } });
  });

  it('completeExchange persists ordinary and store_review session kinds', async () => {
    await AuthStore.completeExchange('ciat_a', { kind: 'ordinary' });
    expect(await CredentialStore.get()).toBe('ciat_a');
    expect(JSON.parse(String(await secureGet(AUTH_SESSION_KEY)))).toEqual({
      kind: 'ordinary',
    });

    await AuthStore.signOut({ discardLocalWork: true });
    await AuthStore.completeExchange('ciat_b', {
      kind: 'store_review',
      provider: 'apple',
    });
    expect(AuthStore.getState()).toEqual({
      status: 'signed_in',
      session: { kind: 'store_review', provider: 'apple' },
    });
  });

  it('signOut moves the bearer to pending revocation and clears active state', async () => {
    await AuthStore.completeExchange('ciat_revoke', { kind: 'ordinary' });
    await PrivateCache.setActiveAccount('user-1');
    await PrivateCache.writeJson('drill', { id: 'd1' });

    const keys: string[] = [];
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async (req) => {
        keys.push(headerMapLower(req.headers).authorization ?? '');
        expect(headerMapLower(req.headers)['idempotency-key']).toMatch(
          /^[0-9a-f]{32}$/i,
        );
        return { status: 204 };
      }),
      getBearer: () => CredentialStore.get(),
    });

    const result = await AuthStore.signOut({ discardLocalWork: true });
    expect(result).toBe('signed_out');
    expect(await CredentialStore.get()).toBeNull();
    expect(await secureGet(AUTH_PENDING_REVOKE_KEY)).toBeNull();
    expect(await PrivateCache.readJson('drill')).toBeNull();
    expect(keys[0]).toBe('Bearer ciat_revoke');
    expect(JSON.stringify(memory.store)).not.toContain('ciat_revoke');
  });

  it('cancels sign-out when staged upload exists and discard is not confirmed', async () => {
    await AuthStore.completeExchange('ciat_keep', { kind: 'ordinary' });
    const result = await AuthStore.signOut({ hasStagedUpload: true });
    expect(result).toBe('cancelled');
    expect(await CredentialStore.get()).toBe('ciat_keep');
  });

  it('blocks completeExchange while pending revocation remains', async () => {
    await memory.setItemAsync(
      AUTH_PENDING_REVOKE_KEY,
      JSON.stringify({
        token: 'ciat_old',
        idempotencyKey: '0123456789abcdef0123456789abcdef',
      }),
    );
    await expect(
      AuthStore.completeExchange('ciat_new', { kind: 'ordinary' }),
    ).rejects.toThrow(/pending token revocation/);
    expect(memory.store.has(AUTH_TOKEN_KEY)).toBe(false);
  });

  it('handleUnauthorized clears credentials and private cache', async () => {
    await AuthStore.completeExchange('ciat_x', { kind: 'ordinary' });
    await PrivateCache.setActiveAccount('u');
    await PrivateCache.writeJson('x', { a: 1 });
    await AuthStore.handleUnauthorized();
    expect(await CredentialStore.get()).toBeNull();
    expect(AuthStore.getState().status).toBe('signed_out');
    expect(await PrivateCache.readJson('x')).toBeNull();
  });
});
