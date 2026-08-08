import {
  AUTH_SESSION_KEY,
  secureDelete,
  secureGet,
  secureSet,
} from '@/platform/secureStore';
import { apiRequest, apiRequestWithStatus } from '@/api/client';
import { createIdempotencyKey } from '@/features/auth/api';
import { createPKCE, normalizeEmailCode } from '@/features/auth/pkce';
import type { SessionKind } from '@/auth/authStore';

export const PRIVACY_PENDING_KEY = 'ci.privacy.replay.v1';
export const PRIVACY_STEPUP_PENDING_KEY = 'ci.auth.stepup.pending.v1';

export type PrivacyPurpose =
  | 'data_export'
  | 'history_reset'
  | 'account_delete';

export type PendingPrivacyOperation = {
  accountId: string;
  purpose: PrivacyPurpose;
  idempotencyKey: string;
  body: Record<string, unknown>;
};

export async function readSessionKind(): Promise<SessionKind | null> {
  const raw = await secureGet(AUTH_SESSION_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as SessionKind;
  } catch {
    return null;
  }
}

export async function savePendingPrivacy(
  op: PendingPrivacyOperation,
): Promise<void> {
  await secureSet(PRIVACY_PENDING_KEY, JSON.stringify(op));
}

export async function readPendingPrivacy(): Promise<PendingPrivacyOperation | null> {
  const raw = await secureGet(PRIVACY_PENDING_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as PendingPrivacyOperation;
  } catch {
    return null;
  }
}

export async function clearPendingPrivacy(): Promise<void> {
  await secureDelete(PRIVACY_PENDING_KEY);
}

export async function startPrivacyStepUp(purpose: PrivacyPurpose): Promise<{
  challengeId: string;
  verifier: string;
}> {
  const { verifier, challenge } = await createPKCE();
  const response = await apiRequest<{
    challenge_id: string;
    expires_at: number;
  }>('/api/v1/auth/step-up/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ purpose, code_challenge: challenge }),
  });
  await secureSet(
    PRIVACY_STEPUP_PENDING_KEY,
    JSON.stringify({
      challengeId: response.challenge_id,
      verifier,
      purpose,
    }),
  );
  return { challengeId: response.challenge_id, verifier };
}

export async function exchangePrivacyStepUp(input: {
  challengeId: string;
  code: string;
  verifier: string;
  idempotencyKey: string;
}): Promise<string> {
  const response = await apiRequest<{ step_up_token: string }>(
    '/api/v1/auth/step-up/exchange',
    {
      method: 'POST',
      authenticated: false,
      idempotencyKey: input.idempotencyKey,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: input.challengeId,
        email_code: normalizeEmailCode(input.code),
        code_verifier: input.verifier,
      }),
    },
  );
  await secureDelete(PRIVACY_STEPUP_PENDING_KEY);
  return response.step_up_token;
}

export async function createPrivacyExport(stepUpToken: string): Promise<{
  export_id: string;
  status: string;
}> {
  const key = await createIdempotencyKey();
  return apiRequest('/api/v1/privacy/exports', {
    method: 'POST',
    idempotencyKey: key,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ step_up_token: stepUpToken }),
  });
}

export async function requestHistoryReset(input: {
  stepUpToken: string;
  expectedHistoryEpoch: number;
  accountId: string;
}): Promise<'pending' | 'done'> {
  const key = await createIdempotencyKey();
  const body = {
    step_up_token: input.stepUpToken,
    expected_history_epoch: input.expectedHistoryEpoch,
  };
  await savePendingPrivacy({
    accountId: input.accountId,
    purpose: 'history_reset',
    idempotencyKey: key,
    body,
  });
  const result = await apiRequestWithStatus('/api/v1/privacy/history-reset', {
    method: 'POST',
    idempotencyKey: key,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (result.status === 204) {
    await clearPendingPrivacy();
    return 'done';
  }
  return 'pending';
}

export async function requestAccountDeletion(input: {
  stepUpToken: string;
  accountId: string;
}): Promise<'pending' | 'done'> {
  const key = await createIdempotencyKey();
  const body = { step_up_token: input.stepUpToken };
  await savePendingPrivacy({
    accountId: input.accountId,
    purpose: 'account_delete',
    idempotencyKey: key,
    body,
  });
  const result = await apiRequestWithStatus('/api/v1/account', {
    method: 'DELETE',
    idempotencyKey: key,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (result.status === 204) {
    await clearPendingPrivacy();
    return 'done';
  }
  return 'pending';
}

/** Machine-checked against OpenAPI const for privacy export ZIP max. */
export const MAX_PRIVACY_EXPORT_ZIP_BYTES = 1_100_000_000;
