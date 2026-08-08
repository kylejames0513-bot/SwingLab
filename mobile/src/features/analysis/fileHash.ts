import { fileHash } from '@preeternal/react-native-file-hash';

/** Full-file SHA-256 as lowercase hex. Never loads the whole file into JS. */
export async function fileSha256Hex(uri: string): Promise<string> {
  const digest = await fileHash(uri, { algorithm: 'SHA-256' });
  return digest.toLowerCase();
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return globalThis.btoa(binary);
}
