export type UploadComparison =
  | null
  | {
      mode: 'matched';
      baseline_session_id: string;
      target_fingerprint: string;
      drill_id: string;
    }
  | {
      mode: 'new_context';
      baseline_session_id: string;
      target_fingerprint: string;
      drill_id: string;
    };

export type UploadState =
  | 'preparing'
  | 'reserving'
  | 'uploading'
  | 'paused'
  | 'verifying'
  | 'abort_pending'
  | 'queued'
  | 'processing'
  | 'retryable_failed'
  | 'retrying'
  | 'retry_source_discard_pending'
  | 'refilm_required'
  | 'done'
  | 'failed'
  | 'expired'
  | 'discarded';

export type PendingUpload = {
  localUri: string;
  sourceName: string;
  fileSha256: string;
  fileBytes: number;
  uploadId: string | null;
  offset: number;
  idempotencyKey: string;
  abortIdempotencyKey: string | null;
  accountId: string;
  historyEpoch: number;
  state: UploadState;
  comparison: UploadComparison;
  club: 'driver' | 'fairway-wood' | 'hybrid' | 'iron' | 'wedge';
  hand: 'left' | 'right';
  angle: 'face-on' | 'dtl';
  chunkBytes: number;
  sessionId: string | null;
};

export type UploadProgress = {
  uploadId: string;
  offset: number;
  fileBytes: number;
  state: UploadState;
};
