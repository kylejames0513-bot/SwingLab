import type { PendingUpload, UploadState } from './uploadTypes';

const ALLOWED: Record<UploadState, ReadonlySet<UploadState>> = {
  preparing: new Set(['reserving', 'failed', 'discarded', 'abort_pending']),
  reserving: new Set(['uploading', 'failed', 'expired', 'abort_pending']),
  uploading: new Set([
    'uploading',
    'paused',
    'verifying',
    'failed',
    'expired',
    'abort_pending',
  ]),
  paused: new Set(['uploading', 'abort_pending', 'expired', 'failed']),
  verifying: new Set(['queued', 'failed', 'expired', 'abort_pending']),
  abort_pending: new Set(['discarded', 'failed', 'abort_pending']),
  queued: new Set(['processing', 'failed', 'retryable_failed', 'refilm_required']),
  processing: new Set([
    'done',
    'failed',
    'retryable_failed',
    'refilm_required',
  ]),
  retryable_failed: new Set(['retrying', 'retry_source_discard_pending', 'discarded']),
  retrying: new Set(['queued', 'processing', 'failed', 'retryable_failed']),
  retry_source_discard_pending: new Set(['discarded', 'failed']),
  refilm_required: new Set(['discarded', 'preparing']),
  done: new Set(),
  failed: new Set(['discarded', 'preparing']),
  expired: new Set(['discarded', 'preparing']),
  discarded: new Set(),
};

export function canTransition(from: UploadState, to: UploadState): boolean {
  return ALLOWED[from].has(to);
}

export function transition(
  upload: PendingUpload,
  to: UploadState,
  patch: Partial<PendingUpload> = {},
): PendingUpload {
  if (!canTransition(upload.state, to)) {
    throw new Error(`Illegal upload transition ${upload.state} -> ${to}`);
  }
  return { ...upload, ...patch, state: to };
}

/** Server offset is authoritative; never move backward without re-read. */
export function reconcileUpload(
  local: PendingUpload,
  remote: { offset: number; status: string; upload_id: string },
): PendingUpload {
  const offset = Math.max(local.offset, remote.offset);
  let state = local.state;
  if (remote.status === 'complete') {
    state = local.state === 'verifying' || local.state === 'uploading' ? 'queued' : local.state;
  } else if (remote.status === 'expired' || remote.status === 'aborted') {
    state = remote.status === 'expired' ? 'expired' : 'discarded';
  } else if (local.state === 'paused' || local.state === 'uploading') {
    state = 'uploading';
  }
  if (!canTransition(local.state, state) && state !== local.state) {
    // Keep local state if remote-derived state is illegal; still adopt offset.
    return { ...local, uploadId: remote.upload_id, offset };
  }
  return {
    ...local,
    uploadId: remote.upload_id,
    offset,
    state,
  };
}

export function createPendingUpload(
  input: Omit<PendingUpload, 'state' | 'uploadId' | 'offset' | 'sessionId'> & {
    state?: UploadState;
  },
): PendingUpload {
  return {
    ...input,
    uploadId: null,
    offset: 0,
    sessionId: null,
    state: input.state ?? 'preparing',
  };
}
