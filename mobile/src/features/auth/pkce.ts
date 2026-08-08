import * as Crypto from 'expo-crypto';

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  // btoa is available in RN/Hermes and Jest jsdom-like environments.
  const base64 = globalThis.btoa(binary);
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

/**
 * PKCE S256 with 32 random bytes. Verifier/challenge are base64url without padding.
 */
export async function createPKCE(): Promise<{
  verifier: string;
  challenge: string;
}> {
  const bytes = await Crypto.getRandomBytesAsync(32);
  const verifier = bytesToBase64Url(bytes);
  if (verifier.length < 43 || verifier.length > 128) {
    throw new Error('PKCE verifier length out of range.');
  }
  const digest = await Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    verifier,
    { encoding: Crypto.CryptoEncoding.BASE64 },
  );
  const challenge = digest.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  if (challenge.length !== 43) {
    throw new Error('PKCE challenge must be 43 base64url characters.');
  }
  return { verifier, challenge };
}

/** Normalize grouped eight-digit codes (NNNN-NNNN / spaces) to digits only. */
export function normalizeEmailCode(raw: string): string {
  return raw.replace(/\D/g, '');
}

export function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}
