import { apiRequestWithStatus } from '@/api/client';
import type { components } from '@/api/schema.generated';
import {
  INSTALLATION_ID_KEY,
  secureDelete,
  secureGet,
  secureSet,
} from '@/platform/secureStore';
import * as Crypto from 'expo-crypto';
import { Platform } from 'react-native';

export type NativeAuthStartResponse =
  components['schemas']['NativeAuthStartResponse'];

export type NativeAuthExchangeResult =
  | components['schemas']['NativeAuthExchangeSuccessResponse']
  | components['schemas']['NativeAuthExchangePendingResponse'];

export type PendingAuthRecord = {
  challengeId: string;
  verifier: string;
  idempotencyKey: string;
  kind: 'ordinary' | 'store_review';
  provider?: 'apple' | 'google';
  startedAt: number;
};

export const PKCE_PENDING_KEY = 'ci.auth.pkce.pending.v1';

function bytesToUuid(bytes: Uint8Array): string {
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

export async function getOrCreateInstallationId(): Promise<string> {
  const existing = await secureGet(INSTALLATION_ID_KEY);
  if (existing && /^[0-9a-f-]{36}$/.test(existing)) {
    return existing;
  }
  const bytes = await Crypto.getRandomBytesAsync(16);
  // RFC 4122 version 4
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const id = bytesToUuid(bytes);
  await secureSet(INSTALLATION_ID_KEY, id);
  return id;
}

export async function createIdempotencyKey(): Promise<string> {
  const bytes = await Crypto.getRandomBytesAsync(16);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function defaultDeviceLabel(): string {
  return Platform.OS === 'ios' ? 'iPhone' : Platform.OS === 'android' ? 'Android' : 'Device';
}

export async function savePendingAuth(record: PendingAuthRecord): Promise<void> {
  await secureSet(PKCE_PENDING_KEY, JSON.stringify(record));
}

export async function readPendingAuth(): Promise<PendingAuthRecord | null> {
  const raw = await secureGet(PKCE_PENDING_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as PendingAuthRecord;
  } catch {
    return null;
  }
}

export async function clearPendingAuth(): Promise<void> {
  await secureDelete(PKCE_PENDING_KEY);
}

export async function startEmailSignIn(input: {
  email: string;
  deviceLabel: string;
  installationId: string;
  challenge: string;
}): Promise<NativeAuthStartResponse> {
  return apiRequestWithStatus<NativeAuthStartResponse>('/api/v1/auth/email/start', {
    method: 'POST',
    authenticated: false,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: input.email,
      device_label: input.deviceLabel,
      installation_id: input.installationId,
      code_challenge: input.challenge,
    }),
  }).then((r) => r.data);
}

export async function exchangeEmailSignIn(input: {
  challengeId: string;
  emailCode: string;
  verifier: string;
  idempotencyKey: string;
}): Promise<{ status: number; result: NativeAuthExchangeResult }> {
  const response = await apiRequestWithStatus<NativeAuthExchangeResult>(
    '/api/v1/auth/email/exchange',
    {
      method: 'POST',
      authenticated: false,
      idempotencyKey: input.idempotencyKey,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: input.challengeId,
        email_code: input.emailCode,
        code_verifier: input.verifier,
      }),
    },
  );
  return { status: response.status, result: response.data };
}

export async function startReviewSignIn(input: {
  account: string;
  deviceLabel: string;
  installationId: string;
  challenge: string;
  provider: 'apple' | 'google';
}): Promise<NativeAuthStartResponse> {
  return apiRequestWithStatus<NativeAuthStartResponse>('/api/v1/auth/review/start', {
    method: 'POST',
    authenticated: false,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      account: input.account,
      device_label: input.deviceLabel,
      installation_id: input.installationId,
      code_challenge: input.challenge,
      provider: input.provider,
    }),
  }).then((r) => r.data);
}

export async function exchangeReviewSignIn(input: {
  challengeId: string;
  password: string;
  verifier: string;
  idempotencyKey: string;
}): Promise<{ status: number; result: NativeAuthExchangeResult }> {
  const response = await apiRequestWithStatus<NativeAuthExchangeResult>(
    '/api/v1/auth/review/exchange',
    {
      method: 'POST',
      authenticated: false,
      idempotencyKey: input.idempotencyKey,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: input.challengeId,
        password: input.password,
        code_verifier: input.verifier,
      }),
    },
  );
  return { status: response.status, result: response.data };
}

export async function fetchMe(): Promise<components['schemas']['IdentityResponse']> {
  return apiRequestWithStatus<components['schemas']['IdentityResponse']>(
    '/api/v1/me',
    { method: 'GET' },
  ).then((r) => r.data);
}
