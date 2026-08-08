import { apiRequest } from '@/api/client';
import type { components } from '@/api/schema.generated';
import { PrivateCache } from '@/platform/privateCache';

export type PracticeEvidenceRequest = {
  baseline_session_id: string;
  target_fingerprint: string;
  drill_id: string;
  minutes: 10 | 20 | 45;
  outcome: 'completed' | 'still_working';
  reps: number;
  feel: 'easier' | 'same' | 'harder' | null;
  relative_strike: 'better' | 'same' | 'worse' | 'unknown' | null;
  start_line: 'left' | 'target' | 'right' | 'unknown' | null;
  miss_pattern:
    | 'left'
    | 'right'
    | 'thin'
    | 'fat'
    | 'heel'
    | 'toe'
    | 'mixed'
    | 'none'
    | 'unknown'
    | null;
  expected_history_epoch: number;
};

export type PracticeEvidenceReceipt =
  components['schemas'] extends { PracticeEvidenceReceipt: infer R }
    ? R
    : Record<string, unknown>;

const PENDING_KEY = 'pending_practice_evidence';

export async function submitPracticeEvidence(
  body: PracticeEvidenceRequest,
  idempotencyKey: string,
): Promise<unknown> {
  return apiRequest('/api/v1/practice-evidence', {
    method: 'POST',
    idempotencyKey,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function queuePracticeEvidence(
  body: PracticeEvidenceRequest,
  idempotencyKey: string,
): Promise<void> {
  await PrivateCache.writeJson(PENDING_KEY, {
    body,
    idempotencyKey,
    queuedAt: Date.now(),
  });
}

export async function flushQueuedPracticeEvidence(): Promise<'sent' | 'empty' | 'failed'> {
  const pending = await PrivateCache.readJson<{
    body: PracticeEvidenceRequest;
    idempotencyKey: string;
  }>(PENDING_KEY);
  if (!pending) {
    return 'empty';
  }
  try {
    await submitPracticeEvidence(pending.body, pending.idempotencyKey);
    await PrivateCache.writeJson(PENDING_KEY, null);
    return 'sent';
  } catch {
    return 'failed';
  }
}
