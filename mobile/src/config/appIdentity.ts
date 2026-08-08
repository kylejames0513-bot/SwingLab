import type { AppEnvironment, AppEnvironmentName } from '@/config/env';

export const APP_IDENTITY_HEADER_NAMES = [
  'X-CaddieInsight-Environment',
  'X-CaddieInsight-Platform',
  'X-CaddieInsight-App-Version',
  'X-CaddieInsight-App-Build',
  'X-CaddieInsight-Application-Id',
] as const;

export type AppIdentityHeaderName = (typeof APP_IDENTITY_HEADER_NAMES)[number];

export type AppPlatform = 'ios' | 'android';

export type AppIdentityHeaders = Readonly<{
  'X-CaddieInsight-Environment': AppEnvironmentName;
  'X-CaddieInsight-Platform': AppPlatform;
  'X-CaddieInsight-App-Version': string;
  'X-CaddieInsight-App-Build': string;
  'X-CaddieInsight-Application-Id': string;
}>;

const VERSION_RE = /^(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,2}$/;
const BUILD_RE = /^[1-9][0-9]{0,9}$/;
const APPLICATION_ID_RE = /^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){2,7}$/;

const APPLICATION_ID_POLICY: Record<AppEnvironmentName, readonly string[]> = {
  development: ['com.caddieinsight.app.dev'],
  staging: ['com.caddieinsight.app.staging', 'com.caddieinsight.app'],
  production: ['com.caddieinsight.app'],
};

export type AppIdentityInput = {
  environment: AppEnvironment;
  platform: AppPlatform;
  appVersion: string;
  appBuild: string;
  applicationId: string;
};

function assertExactToken(value: string, label: string): string {
  if (
    !value ||
    value !== value.trim() ||
    value.includes(',') ||
    /\s/.test(value)
  ) {
    throw new Error(`Malformed ${label} for app identity.`);
  }
  return value;
}

/**
 * Derive the closed immutable identity header tuple once from embedded
 * environment + native application metadata. Callers cannot override members
 * via ordinary header maps.
 */
export function deriveAppIdentityHeaders(
  input: AppIdentityInput,
): AppIdentityHeaders {
  const environment = input.environment.environment;
  const platform = input.platform;
  if (platform !== 'ios' && platform !== 'android') {
    throw new Error('App identity platform must be ios or android.');
  }

  const appVersion = assertExactToken(input.appVersion, 'app version');
  const appBuild = assertExactToken(input.appBuild, 'app build');
  const applicationId = assertExactToken(input.applicationId, 'application id');

  if (!VERSION_RE.test(appVersion)) {
    throw new Error('App identity version is malformed.');
  }
  if (!BUILD_RE.test(appBuild)) {
    throw new Error('App identity build is malformed.');
  }
  if (!APPLICATION_ID_RE.test(applicationId)) {
    throw new Error('App identity application id is malformed.');
  }

  const allowed = APPLICATION_ID_POLICY[environment];
  if (!allowed.includes(applicationId)) {
    throw new Error(
      `Application id ${applicationId} is not allowed for ${environment}.`,
    );
  }

  return Object.freeze({
    'X-CaddieInsight-Environment': environment,
    'X-CaddieInsight-Platform': platform,
    'X-CaddieInsight-App-Version': appVersion,
    'X-CaddieInsight-App-Build': appBuild,
    'X-CaddieInsight-Application-Id': applicationId,
  });
}

export function appIdentityHeadersRecord(
  headers: AppIdentityHeaders,
): Record<AppIdentityHeaderName, string> {
  return {
    'X-CaddieInsight-Environment': headers['X-CaddieInsight-Environment'],
    'X-CaddieInsight-Platform': headers['X-CaddieInsight-Platform'],
    'X-CaddieInsight-App-Version': headers['X-CaddieInsight-App-Version'],
    'X-CaddieInsight-App-Build': headers['X-CaddieInsight-App-Build'],
    'X-CaddieInsight-Application-Id': headers['X-CaddieInsight-Application-Id'],
  };
}
