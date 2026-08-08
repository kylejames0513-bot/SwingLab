import { apiRequest, apiRequestWithStatus } from '@/api/client';
import type { components } from '@/api/schema.generated';
import type { PendingUpload, UploadComparison } from './uploadTypes';

export type UploadReservationResponse =
  components['schemas']['UploadReservationResponse'];
export type MobileSessionResponse =
  components['schemas']['MobileSessionResponse'];

export async function createUploadReservation(input: {
  pending: PendingUpload;
  sourceName: string;
}): Promise<UploadReservationResponse> {
  const body = {
    source_name: input.sourceName,
    file_sha256: input.pending.fileSha256,
    file_bytes: input.pending.fileBytes,
    club: input.pending.club,
    hand: input.pending.hand,
    angle: input.pending.angle,
    comparison: input.pending.comparison,
    expected_history_epoch: input.pending.historyEpoch,
  };
  return apiRequest<UploadReservationResponse>('/api/v1/uploads', {
    method: 'POST',
    idempotencyKey: input.pending.idempotencyKey,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getUploadStatus(
  uploadId: string,
): Promise<UploadReservationResponse> {
  return apiRequest<UploadReservationResponse>(`/api/v1/uploads/${uploadId}`);
}

export async function putUploadChunk(input: {
  uploadId: string;
  offset: number;
  chunk: Uint8Array;
  chunkSha256Base64: string;
  signal?: AbortSignal;
}): Promise<UploadReservationResponse> {
  // Body must be the raw chunk bytes; never wrap as FormData/base64.
  return apiRequest<UploadReservationResponse>(
    `/api/v1/uploads/${input.uploadId}`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/offset+octet-stream',
        'Upload-Offset': String(input.offset),
        'Upload-Checksum': input.chunkSha256Base64,
      },
      body: input.chunk as unknown as BodyInit,
      signal: input.signal,
      retryOnNetwork: false,
    },
  );
}

export async function completeUpload(
  uploadId: string,
  idempotencyKey: string,
): Promise<{ session_id?: string } & Record<string, unknown>> {
  return apiRequest(`/api/v1/uploads/${uploadId}/complete`, {
    method: 'POST',
    idempotencyKey,
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
}

export async function abortUpload(
  uploadId: string,
  idempotencyKey: string,
): Promise<'aborted' | 'pending'> {
  const result = await apiRequestWithStatus(`/api/v1/uploads/${uploadId}`, {
    method: 'DELETE',
    idempotencyKey,
  });
  if (result.status === 202) {
    return 'pending';
  }
  return 'aborted';
}

export async function fetchMobileSession(
  sessionId: string,
): Promise<MobileSessionResponse> {
  return apiRequest<MobileSessionResponse>(
    `/api/v1/mobile/sessions/${sessionId}`,
  );
}

export function encodeComparison(comparison: UploadComparison): UploadComparison {
  return comparison;
}
