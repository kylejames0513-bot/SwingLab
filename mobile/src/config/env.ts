export type AppEnvironmentName = 'development' | 'staging' | 'production';

export type AppEnvironment = {
  apiBaseUrl: URL;
  apiOrigin: string;
  environment: AppEnvironmentName;
  buildProfile: string;
  easProjectId: string | null;
  environmentIdentity: string;
};

const ENVIRONMENTS = new Set<AppEnvironmentName>([
  'development',
  'staging',
  'production',
]);

/** Public Expo env keys that look like secrets must never enter the bundle. */
const FORBIDDEN_PUBLIC_ENV_KEYS = [
  'EXPO_PUBLIC_API_KEY',
  'EXPO_PUBLIC_SECRET',
  'EXPO_PUBLIC_TOKEN',
  'EXPO_PUBLIC_PASSWORD',
  'EXPO_PUBLIC_PRIVATE_KEY',
  'EXPO_PUBLIC_CLIENT_SECRET',
  'EXPO_PUBLIC_ACCESS_TOKEN',
] as const;

function assertNoSecretShapedPublicEnv(
  env: NodeJS.ProcessEnv | Record<string, string | undefined>,
): void {
  for (const key of FORBIDDEN_PUBLIC_ENV_KEYS) {
    if (env[key] != null && String(env[key]).length > 0) {
      throw new Error(
        `Secret-shaped public environment variable ${key} is not allowed in the mobile bundle.`,
      );
    }
  }
}

function parseEnvironment(raw: string | undefined): AppEnvironmentName {
  const value = (raw ?? 'development').trim().toLowerCase();
  if (!ENVIRONMENTS.has(value as AppEnvironmentName)) {
    throw new Error(
      `EXPO_PUBLIC_APP_ENV must be development, staging, or production (got ${raw ?? 'undefined'}).`,
    );
  }
  return value as AppEnvironmentName;
}

function normalizeApiBaseUrl(raw: string, environment: AppEnvironmentName): URL {
  const trimmed = raw.trim();
  if (!trimmed) {
    throw new Error('EXPO_PUBLIC_API_BASE_URL is required.');
  }

  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error(`EXPO_PUBLIC_API_BASE_URL is not a valid URL: ${raw}`);
  }

  if (environment === 'production' && url.protocol !== 'https:') {
    throw new Error('Production EXPO_PUBLIC_API_BASE_URL must use HTTPS.');
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('EXPO_PUBLIC_API_BASE_URL must use http or https.');
  }

  // Normalize once: drop a single trailing slash on pathname (root becomes "/").
  if (url.pathname.length > 1 && url.pathname.endsWith('/')) {
    url.pathname = url.pathname.replace(/\/+$/, '') || '/';
  }

  return url;
}

function defaultBundleIdentity(environment: AppEnvironmentName): string {
  if (environment === 'development') {
    return 'com.caddieinsight.app.dev';
  }
  if (environment === 'staging') {
    return 'com.caddieinsight.app.staging';
  }
  return 'com.caddieinsight.app';
}

export type ResolveAppEnvironmentInput = {
  apiBaseUrl?: string;
  appEnv?: string;
  buildProfile?: string;
  easProjectId?: string | null;
  bundleIdentity?: string;
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>;
};

/**
 * Resolve public app environment from EXPO_PUBLIC_* values only.
 * Never reads provider credentials or other secrets into the bundle.
 */
export function resolveAppEnvironment(
  input: ResolveAppEnvironmentInput = {},
): AppEnvironment {
  const env = input.env ?? process.env;
  assertNoSecretShapedPublicEnv(env);

  const environment = parseEnvironment(
    input.appEnv ?? env.EXPO_PUBLIC_APP_ENV,
  );
  const apiBaseRaw =
    input.apiBaseUrl ?? env.EXPO_PUBLIC_API_BASE_URL ?? '';

  if (environment === 'production' && !apiBaseRaw.trim()) {
    throw new Error(
      'Missing EXPO_PUBLIC_API_BASE_URL for production; cannot render.',
    );
  }

  if (!apiBaseRaw.trim()) {
    throw new Error('EXPO_PUBLIC_API_BASE_URL is required.');
  }

  const apiBaseUrl = normalizeApiBaseUrl(apiBaseRaw, environment);
  const apiOrigin = apiBaseUrl.origin;
  const buildProfile =
    input.buildProfile ??
    env.EAS_BUILD_PROFILE ??
    env.EXPO_PUBLIC_BUILD_PROFILE ??
    environment;
  const easProjectId =
    input.easProjectId !== undefined
      ? input.easProjectId
      : env.EXPO_PUBLIC_EAS_PROJECT_ID?.trim() || null;
  const bundleIdentity =
    input.bundleIdentity ?? defaultBundleIdentity(environment);

  const environmentIdentity = [
    environment,
    apiOrigin,
    buildProfile,
    bundleIdentity,
  ].join('|');

  return {
    apiBaseUrl,
    apiOrigin,
    environment,
    buildProfile,
    easProjectId,
    environmentIdentity,
  };
}

let cached: AppEnvironment | null = null;

/** Lazy singleton for runtime use; throws before render if misconfigured. */
export function getAppEnvironment(): AppEnvironment {
  if (!cached) {
    cached = resolveAppEnvironment();
  }
  return cached;
}

/** Test helper to clear the singleton. */
export function resetAppEnvironmentCache(): void {
  cached = null;
}
