import * as Crypto from 'expo-crypto';

import { readBoundedChunk } from '@/platform/files';
import { bytesToBase64, fileSha256Hex } from './fileHash';
import {
  completeUpload,
  createUploadReservation,
  getUploadStatus,
  putUploadChunk,
} from './uploadApi';
import {
  createPendingUpload,
  reconcileUpload,
  transition,
} from './uploadMachine';
import { uploadRepository } from './uploadRepository';
import type {
  PendingUpload,
  UploadComparison,
  UploadProgress,
  UploadState,
} from './uploadTypes';

export type HashAndUploadInput = {
  localUri: string;
  sourceName: string;
  fileBytes: number;
  accountId: string;
  historyEpoch: number;
  club: PendingUpload['club'];
  hand: PendingUpload['hand'];
  angle: PendingUpload['angle'];
  comparison: UploadComparison;
  chunkBytes: number;
  idempotencyKey: string;
  signal?: AbortSignal;
  /** Injected for tests — defaults to real bounded reader + crypto. */
  readChunk?: typeof readBoundedChunk;
  hashFile?: typeof fileSha256Hex;
  hashChunk?: (bytes: Uint8Array) => Promise<string>;
};

async function defaultHashChunk(bytes: Uint8Array): Promise<string> {
  // Expo Crypto expects a string; hash hex of bytes via digestStringAsync on latin1.
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  const digest = await Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    binary,
    { encoding: Crypto.CryptoEncoding.BASE64 },
  );
  return digest;
}

function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    const error = new Error('Upload aborted');
    error.name = 'AbortError';
    throw error;
  }
}

/**
 * Hash the full file once, reserve, then stream server-bounded chunks.
 * Yields progress after each acknowledged server offset. Backgrounding should
 * abort `signal` and transition to paused outside this generator.
 */
export async function* hashAndUpload(
  input: HashAndUploadInput,
): AsyncGenerator<UploadProgress, PendingUpload> {
  const readChunk = input.readChunk ?? readBoundedChunk;
  const hashFile = input.hashFile ?? fileSha256Hex;
  const hashChunk = input.hashChunk ?? defaultHashChunk;

  let pending = createPendingUpload({
    localUri: input.localUri,
    sourceName: input.sourceName,
    fileSha256: '',
    fileBytes: input.fileBytes,
    idempotencyKey: input.idempotencyKey,
    abortIdempotencyKey: null,
    accountId: input.accountId,
    historyEpoch: input.historyEpoch,
    comparison: input.comparison,
    club: input.club,
    hand: input.hand,
    angle: input.angle,
    chunkBytes: input.chunkBytes,
  });
  await uploadRepository.save(pending);
  yield progress(pending);

  assertNotAborted(input.signal);
  const fileSha256 = await hashFile(input.localUri);
  pending = { ...pending, fileSha256 };
  pending = transition(pending, 'reserving');
  await uploadRepository.save(pending);
  yield progress(pending);

  assertNotAborted(input.signal);
  const reservation = await createUploadReservation({
    pending,
    sourceName: input.sourceName,
  });
  pending = transition(pending, 'uploading', {
    uploadId: reservation.upload_id,
    offset: reservation.offset,
    chunkBytes: reservation.chunk_bytes,
  });
  await uploadRepository.save(pending);
  yield progress(pending);

  while (pending.offset < pending.fileBytes) {
    assertNotAborted(input.signal);
    if (!pending.uploadId) {
      throw new Error('Upload reservation missing upload_id.');
    }

    const remaining = pending.fileBytes - pending.offset;
    const chunkLength = Math.min(pending.chunkBytes, remaining);
    const chunk = await readChunk(pending.localUri, pending.offset, chunkLength);
    if (chunk.byteLength === 0) {
      throw new Error('Unexpected EOF while reading upload chunk.');
    }

    const checksum = await hashChunk(chunk);
    const checksumB64 = /^[0-9a-f]{64}$/i.test(checksum)
      ? bytesToBase64(hexToBytes(checksum))
      : checksum;

    try {
      const updated = await putUploadChunk({
        uploadId: pending.uploadId,
        offset: pending.offset,
        chunk,
        chunkSha256Base64: checksumB64,
        signal: input.signal,
      });
      pending = {
        ...pending,
        offset: updated.offset,
        chunkBytes: updated.chunk_bytes,
        state: 'uploading' as UploadState,
      };
      await uploadRepository.save(pending);
      yield progress(pending);
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        pending = transition(pending, 'paused');
        await uploadRepository.save(pending);
        yield progress(pending);
        return pending;
      }
      throw error;
    }
  }

  pending = transition(pending, 'verifying');
  await uploadRepository.save(pending);
  yield progress(pending);

  assertNotAborted(input.signal);
  const completed = await completeUpload(
    pending.uploadId!,
    input.idempotencyKey,
  );
  const sessionId =
    typeof completed.session_id === 'string'
      ? completed.session_id
      : typeof (completed as { session?: { id?: string } }).session?.id ===
          'string'
        ? (completed as { session: { id: string } }).session.id
        : null;

  pending = transition(pending, 'queued', { sessionId });
  await uploadRepository.save(pending);
  yield progress(pending);
  return pending;
}

export async function resumeUpload(
  pending: PendingUpload,
  signal?: AbortSignal,
): Promise<AsyncGenerator<UploadProgress, PendingUpload>> {
  if (!pending.uploadId) {
    throw new Error('Cannot resume without upload_id.');
  }
  const remote = await getUploadStatus(pending.uploadId);
  const reconciled = reconcileUpload(pending, {
    upload_id: remote.upload_id,
    offset: remote.offset,
    status: remote.status,
  });
  await uploadRepository.save(reconciled);
  return hashAndUpload({
    localUri: reconciled.localUri,
    sourceName: reconciled.sourceName,
    fileBytes: reconciled.fileBytes,
    accountId: reconciled.accountId,
    historyEpoch: reconciled.historyEpoch,
    club: reconciled.club,
    hand: reconciled.hand,
    angle: reconciled.angle,
    comparison: reconciled.comparison,
    chunkBytes: reconciled.chunkBytes,
    idempotencyKey: reconciled.idempotencyKey,
    signal,
  });
}

function progress(pending: PendingUpload): UploadProgress {
  return {
    uploadId: pending.uploadId ?? '',
    offset: pending.offset,
    fileBytes: pending.fileBytes,
    state: pending.state,
  };
}

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.replace(/[^0-9a-f]/gi, '');
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = Number.parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}
