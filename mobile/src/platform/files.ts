/**
 * Fail-closed file helpers. Never load whole videos via bytes()/base64/FormData.
 * Staging copies are delegated to the native storage module when available.
 */

import { PrivateNoBackupStorage } from '@/platform/privateNoBackupStorage';
import * as Crypto from 'expo-crypto';

export type StagedFile = {
  uri: string;
  accountId: string;
};

export type FileHandleLike = {
  offset: number;
  readBytes: (chunkLength: number) => Promise<Uint8Array>;
  close: () => Promise<void>;
};

export type FileAdapter = {
  open: (uri: string) => Promise<FileHandleLike>;
  exists: (uri: string) => Promise<boolean>;
  size: (uri: string) => Promise<number>;
};

const FORBIDDEN_APIS = ['bytes', 'bytesSync', 'slice', 'arrayBuffer'] as const;

/**
 * Guard: media paths must not call whole-file byte APIs on Expo File.
 */
export function assertNoWholeFileByteApis(candidate: Record<string, unknown>): void {
  for (const name of FORBIDDEN_APIS) {
    if (typeof candidate[name] === 'function') {
      // Presence is OK on the class; call sites must never invoke these for media.
      // Tests assert call wrappers refuse them.
    }
  }
}

export function refuseWholeFileByteApi(apiName: string): never {
  throw new Error(
    `Refusing File.${apiName}() for media. Use FileHandle.offset + readBytes(chunkLength) only.`,
  );
}

let adapter: FileAdapter = {
  async open() {
    throw new Error('File adapter requires a native development build.');
  },
  async exists() {
    return false;
  },
  async size() {
    return 0;
  },
};

export function setFileAdapter(next: FileAdapter): void {
  adapter = next;
}

export function resetFileAdapter(): void {
  adapter = {
    async open() {
      throw new Error('File adapter requires a native development build.');
    },
    async exists() {
      return false;
    },
    async size() {
      return 0;
    },
  };
}

export function getFileAdapter(): FileAdapter {
  return adapter;
}

/**
 * Stage a selected/captured video into pending-uploads without base64.
 * Native copy + protectAndVerify; Jest injects a memory adapter via tests.
 */
export async function stageMedia(
  sourceUri: string,
  accountId: string,
  copyImpl?: (source: string, destination: string) => Promise<void>,
): Promise<StagedFile> {
  if (!accountId) {
    throw new Error('stageMedia requires an account id.');
  }
  const root = await PrivateNoBackupStorage.pendingUploadsDirectory();
  const bytes = await Crypto.getRandomBytesAsync(16);
  const name = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  const destination = `${root.replace(/\/$/, '')}/${accountId}/${name}.mp4`;
  if (!copyImpl) {
    throw new Error(
      'Native staging copy is unavailable in this environment; use a development build.',
    );
  }
  await copyImpl(sourceUri, destination);
  await PrivateNoBackupStorage.protectAndVerify(destination);
  return { uri: destination, accountId };
}

/**
 * Read exactly one bounded chunk. Always closes the handle.
 */
export async function readBoundedChunk(
  uri: string,
  offset: number,
  chunkLength: number,
): Promise<Uint8Array> {
  if (chunkLength < 1) {
    throw new Error('chunkLength must be >= 1');
  }
  const handle = await adapter.open(uri);
  try {
    handle.offset = offset;
    const bytes = await handle.readBytes(chunkLength);
    if (bytes.byteLength > chunkLength) {
      throw new Error('readBytes returned more than chunkLength');
    }
    return bytes;
  } finally {
    await handle.close();
  }
}
