import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';

describe('app identity headers', () => {
  const env = resolveAppEnvironment({
    appEnv: 'development',
    apiBaseUrl: 'https://api.example.com',
    env: {},
  });

  it('derives the closed five-tuple for a development build', () => {
    const headers = deriveAppIdentityHeaders({
      environment: env,
      platform: 'ios',
      appVersion: '1.0.0',
      appBuild: '42',
      applicationId: 'com.caddieinsight.app.dev',
    });
    expect(headers).toEqual({
      'X-CaddieInsight-Environment': 'development',
      'X-CaddieInsight-Platform': 'ios',
      'X-CaddieInsight-App-Version': '1.0.0',
      'X-CaddieInsight-App-Build': '42',
      'X-CaddieInsight-Application-Id': 'com.caddieinsight.app.dev',
    });
  });

  it('rejects production application ids outside the allowlist', () => {
    const production = resolveAppEnvironment({
      appEnv: 'production',
      apiBaseUrl: 'https://api.caddieinsight.com',
      env: {},
    });
    expect(() =>
      deriveAppIdentityHeaders({
        environment: production,
        platform: 'android',
        appVersion: '1.2',
        appBuild: '9',
        applicationId: 'com.caddieinsight.app.dev',
      }),
    ).toThrow(/not allowed/);
  });

  it('rejects malformed version and build tokens', () => {
    expect(() =>
      deriveAppIdentityHeaders({
        environment: env,
        platform: 'ios',
        appVersion: '01.0.0',
        appBuild: '1',
        applicationId: 'com.caddieinsight.app.dev',
      }),
    ).toThrow(/version/);
    expect(() =>
      deriveAppIdentityHeaders({
        environment: env,
        platform: 'ios',
        appVersion: '1.0.0',
        appBuild: '0',
        applicationId: 'com.caddieinsight.app.dev',
      }),
    ).toThrow(/build/);
  });
});
