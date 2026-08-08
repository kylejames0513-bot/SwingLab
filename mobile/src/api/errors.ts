export type AppErrorCategory =
  | 'network'
  | 'auth'
  | 'authorization'
  | 'not_found'
  | 'conflict'
  | 'validation'
  | 'rate_limit'
  | 'capacity'
  | 'server'
  | 'unknown';

export type AppError = {
  category: AppErrorCategory;
  apiCode: string | null;
  status: number | null;
  retryable: boolean;
  retryAfterSeconds: number | null;
  referenceId: string | null;
  authenticate: string | null;
};

const MAX_HEADER_VALUE = 256;
const MAX_REFERENCE_ID = 128;
const MAX_API_CODE = 128;

/** Allowlisted API codes that may drive customer-facing copy. */
export const CUSTOMER_COPY_API_CODES = new Set([
  'authentication_rejected',
  'bearer_required',
  'rate_limited',
  'insufficient_storage',
  'history_epoch_conflict',
  'device_limit',
  'upload_expired',
  'upload_conflict',
  'comparison_conflict',
  'export_expired',
  'export_pending',
  'deletion_pending',
  'history_reset_pending',
  'source_unavailable_after_restore',
  'validation_error',
  'not_found',
]);

function boundHeader(value: string | null, max: number): string | null {
  if (value == null) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  return trimmed.length > max ? trimmed.slice(0, max) : trimmed;
}

function parseRetryAfterSeconds(raw: string | null): number | null {
  if (raw == null || !raw.trim()) {
    return null;
  }
  const value = raw.trim();
  if (/^\d+$/.test(value)) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) {
      return null;
    }
    return Math.min(seconds, 86_400);
  }
  const when = Date.parse(value);
  if (Number.isNaN(when)) {
    return null;
  }
  const delta = Math.ceil((when - Date.now()) / 1000);
  if (!Number.isFinite(delta)) {
    return null;
  }
  return Math.min(Math.max(delta, 0), 86_400);
}

function categoryForStatus(status: number | null, apiCode: string | null): AppErrorCategory {
  if (status === 401) {
    return 'auth';
  }
  if (status === 403) {
    return 'authorization';
  }
  if (status === 404) {
    return 'not_found';
  }
  if (status === 409) {
    return 'conflict';
  }
  if (status === 422 || status === 400) {
    return 'validation';
  }
  if (status === 429) {
    return 'rate_limit';
  }
  if (status === 507) {
    return 'capacity';
  }
  if (status != null && status >= 500) {
    return 'server';
  }
  if (apiCode === 'deletion_pending' || apiCode === 'history_reset_pending') {
    return 'conflict';
  }
  if (status == null) {
    return 'network';
  }
  return 'unknown';
}

export type TranslateErrorInput = {
  status?: number | null;
  body?: unknown;
  retryAfterHeader?: string | null;
  wwwAuthenticate?: string | null;
  networkFailure?: boolean;
};

/**
 * Normalize transport failures into AppError. Message text never drives control flow.
 */
export function translateToAppError(input: TranslateErrorInput): AppError {
  if (input.networkFailure) {
    return {
      category: 'network',
      apiCode: null,
      status: null,
      retryable: true,
      retryAfterSeconds: null,
      referenceId: null,
      authenticate: null,
    };
  }

  const status = input.status ?? null;
  let apiCode: string | null = null;
  let retryable = false;
  let referenceId: string | null = null;

  const body = input.body;
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    const record = body as Record<string, unknown>;
    if (typeof record.code === 'string') {
      apiCode = boundHeader(record.code, MAX_API_CODE);
    }
    if (typeof record.retryable === 'boolean') {
      retryable = record.retryable;
    }
    if (typeof record.reference_id === 'string') {
      referenceId = boundHeader(record.reference_id, MAX_REFERENCE_ID);
    }
    // Semantic labels for privacy erasure pending (202 body status, not APIError.code).
    if (
      status === 202 &&
      record.status === 'pending' &&
      typeof record.retry_after_seconds === 'number'
    ) {
      // Callers may specialize; keep a stable code for UI branching.
      apiCode = apiCode ?? 'history_reset_pending';
      retryable = true;
    }
  }

  if (status === 429 || status === 503 || status === 202) {
    retryable = true;
  }
  if (status != null && status >= 500) {
    retryable = true;
  }

  return {
    category: categoryForStatus(status, apiCode),
    apiCode,
    status,
    retryable,
    retryAfterSeconds: parseRetryAfterSeconds(
      boundHeader(input.retryAfterHeader ?? null, MAX_HEADER_VALUE),
    ),
    referenceId,
    authenticate: boundHeader(input.wwwAuthenticate ?? null, MAX_HEADER_VALUE),
  };
}

export function isCustomerCopyCode(apiCode: string | null): boolean {
  return apiCode != null && CUSTOMER_COPY_API_CODES.has(apiCode);
}

export class ApiRequestError extends Error {
  readonly appError: AppError;

  constructor(appError: AppError) {
    super(`API error ${appError.status ?? 'network'}:${appError.apiCode ?? 'unknown'}`);
    this.name = 'ApiRequestError';
    this.appError = appError;
  }
}
