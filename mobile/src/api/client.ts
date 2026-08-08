import type { AppIdentityHeaders } from '@/config/appIdentity';
import { appIdentityHeadersRecord } from '@/config/appIdentity';
import { CredentialStore } from '@/platform/secureStore';

import { ApiRequestError, translateToAppError } from './errors';

export type ApiRequestOptions = {
  method?: string;
  body?: BodyInit | null;
  headers?: Record<string, string>;
  idempotencyKey?: string;
  /** When false, skip Authorization even if a credential exists. Default true. */
  authenticated?: boolean;
  /** Override bearer for pending-revocation only — never for ordinary app traffic. */
  bearerOverride?: string | null;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Idempotent methods may retry once on network failure. */
  retryOnNetwork?: boolean;
};

export type ApiClientConfig = {
  baseUrl: string;
  identity: AppIdentityHeaders;
  fetchImpl?: typeof fetch;
  getBearer?: () => Promise<string | null>;
  onUnauthorized?: () => Promise<void> | void;
};

const DEFAULT_TIMEOUT_MS = 30_000;

let activeConfig: ApiClientConfig | null = null;

export function configureApiClient(config: ApiClientConfig): void {
  activeConfig = config;
}

export function resetApiClient(): void {
  activeConfig = null;
}

export function getApiClientConfig(): ApiClientConfig {
  if (!activeConfig) {
    throw new Error('API client is not configured; EnvironmentBoundary must succeed first.');
  }
  return activeConfig;
}

function isIdempotentMethod(method: string): boolean {
  const upper = method.toUpperCase();
  return upper === 'GET' || upper === 'HEAD' || upper === 'OPTIONS';
}

function joinUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, '');
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

function assertNoOrigin(headers: Headers): void {
  if (headers.has('Origin') || headers.has('origin')) {
    throw new Error('Native API client must not synthesize an Origin header.');
  }
}

function redactForLog(value: string): string {
  if (value.toLowerCase().startsWith('bearer ')) {
    return 'Bearer [redacted]';
  }
  if (value.startsWith('ciat_')) {
    return '[redacted-token]';
  }
  return value;
}

export type ApiSuccess<T> = {
  status: number;
  data: T;
};

/**
 * Central authenticated JSON transport. Does not expose openapi-fetch's raw client.
 */
export async function apiRequestWithStatus<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<ApiSuccess<T>> {
  const config = getApiClientConfig();
  const method = (options.method ?? 'GET').toUpperCase();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const fetchImpl = config.fetchImpl ?? fetch;
  const shouldRetryNetwork =
    options.retryOnNetwork ??
    (isIdempotentMethod(method) || Boolean(options.idempotencyKey));

  const attempt = async (): Promise<ApiSuccess<T>> => {
    const headers = new Headers();
    headers.set('Accept', 'application/json');
    const identity = appIdentityHeadersRecord(config.identity);
    for (const [name, value] of Object.entries(identity)) {
      headers.set(name, value);
    }

    if (options.headers) {
      for (const [name, value] of Object.entries(options.headers)) {
        const lower = name.toLowerCase();
        if (lower.startsWith('x-caddieinsight-')) {
          throw new Error('App identity headers cannot be overridden by callers.');
        }
        if (lower === 'origin') {
          throw new Error('Native API client must not synthesize an Origin header.');
        }
        if (lower === 'authorization') {
          throw new Error('Authorization must come from CredentialStore, not caller headers.');
        }
        headers.set(name, value);
      }
    }

    if (options.idempotencyKey) {
      headers.set('Idempotency-Key', options.idempotencyKey);
    }

    const authenticated = options.authenticated !== false;
    if (authenticated) {
      const bearer =
        options.bearerOverride !== undefined
          ? options.bearerOverride
          : config.getBearer
            ? await config.getBearer()
            : await CredentialStore.get();
      if (bearer) {
        headers.set('Authorization', `Bearer ${bearer}`);
      }
    }

    assertNoOrigin(headers);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const onExternalAbort = () => controller.abort();
    options.signal?.addEventListener('abort', onExternalAbort);

    let response: Response;
    try {
      response = await fetchImpl(joinUrl(config.baseUrl, path), {
        method,
        headers,
        body: options.body ?? null,
        signal: controller.signal,
      });
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        throw new ApiRequestError(
          translateToAppError({ networkFailure: true, status: null }),
        );
      }
      throw new ApiRequestError(translateToAppError({ networkFailure: true }));
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', onExternalAbort);
    }

    const wwwAuthenticate = response.headers.get('WWW-Authenticate');
    const retryAfter = response.headers.get('Retry-After');
    const contentType = response.headers.get('Content-Type') ?? '';
    let body: unknown = null;
    const rawText = await response.text();
    if (rawText) {
      if (contentType.includes('application/json')) {
        try {
          body = JSON.parse(rawText) as unknown;
        } catch {
          throw new ApiRequestError(
            translateToAppError({
              status: response.status,
              body: { code: 'malformed_json', retryable: false },
              retryAfterHeader: retryAfter,
              wwwAuthenticate,
            }),
          );
        }
      } else {
        body = { code: 'non_json_body', message: redactForLog(rawText.slice(0, 64)) };
      }
    }

    if (response.status === 401) {
      await config.onUnauthorized?.();
      throw new ApiRequestError(
        translateToAppError({
          status: 401,
          body,
          retryAfterHeader: retryAfter,
          wwwAuthenticate,
        }),
      );
    }

    if (!response.ok) {
      throw new ApiRequestError(
        translateToAppError({
          status: response.status,
          body,
          retryAfterHeader: retryAfter,
          wwwAuthenticate,
        }),
      );
    }

    if (response.status === 204 || rawText === '') {
      return { status: response.status, data: undefined as T };
    }

    return { status: response.status, data: body as T };
  };

  try {
    return await attempt();
  } catch (error) {
    if (
      shouldRetryNetwork &&
      error instanceof ApiRequestError &&
      error.appError.category === 'network'
    ) {
      return attempt();
    }
    throw error;
  }
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const result = await apiRequestWithStatus<T>(path, options);
  return result.data;
}
