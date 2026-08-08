import {
  resolveAppEnvironment,
  resetAppEnvironmentCache,
} from '../../src/config/env';

describe('resolveAppEnvironment', () => {
  afterEach(() => {
    resetAppEnvironmentCache();
  });

  it('requires HTTPS for production', () => {
    expect(() =>
      resolveAppEnvironment({
        appEnv: 'production',
        apiBaseUrl: 'http://api.example.com',
        env: {},
      }),
    ).toThrow(/HTTPS/i);
  });

  it('throws before render when production URL is missing', () => {
    expect(() =>
      resolveAppEnvironment({
        appEnv: 'production',
        env: { EXPO_PUBLIC_APP_ENV: 'production' },
      }),
    ).toThrow(/EXPO_PUBLIC_API_BASE_URL/);
  });

  it('normalizes a single trailing slash on the API base URL', () => {
    const env = resolveAppEnvironment({
      appEnv: 'development',
      apiBaseUrl: 'https://api.example.com/v1/',
      env: {},
    });
    expect(env.apiBaseUrl.href).toBe('https://api.example.com/v1');
    expect(env.apiOrigin).toBe('https://api.example.com');
  });

  it('does not double-strip path segments when normalizing', () => {
    const env = resolveAppEnvironment({
      appEnv: 'staging',
      apiBaseUrl: 'https://staging.example.com/api',
      env: {},
    });
    expect(env.apiBaseUrl.pathname).toBe('/api');
  });

  it('rejects secret-shaped public environment variables', () => {
    expect(() =>
      resolveAppEnvironment({
        appEnv: 'development',
        apiBaseUrl: 'https://api.example.com',
        env: { EXPO_PUBLIC_API_KEY: 'secret-value' },
      }),
    ).toThrow(/Secret-shaped/);
  });

  it('builds an immutable environmentIdentity without secrets', () => {
    const env = resolveAppEnvironment({
      appEnv: 'production',
      apiBaseUrl: 'https://api.caddieinsight.com/',
      buildProfile: 'production',
      easProjectId: 'proj-123',
      bundleIdentity: 'com.caddieinsight.app',
      env: {},
    });
    expect(env.environment).toBe('production');
    expect(env.apiOrigin).toBe('https://api.caddieinsight.com');
    expect(env.easProjectId).toBe('proj-123');
    expect(env.environmentIdentity).toBe(
      'production|https://api.caddieinsight.com|production|com.caddieinsight.app',
    );
    expect(env.environmentIdentity).not.toMatch(/secret|token|key/i);
  });
});
