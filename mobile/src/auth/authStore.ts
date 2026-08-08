import { apiRequest, configureApiClient, resetApiClient } from '@/api/client';
import { createQueryClient } from '@/api/queryClient';
import type { AppIdentityHeaders } from '@/config/appIdentity';
import type { AppEnvironment } from '@/config/env';
import { PrivateCache } from '@/platform/privateCache';
import {
  AUTH_PENDING_REVOKE_KEY,
  AUTH_SESSION_KEY,
  CredentialStore,
  secureDelete,
  secureGet,
  secureSet,
} from '@/platform/secureStore';
import * as Crypto from 'expo-crypto';

export type SessionKind =
  | { kind: 'ordinary' }
  | { kind: 'store_review'; provider: 'apple' | 'google' };

export type AuthState =
  | { status: 'signed_out' }
  | { status: 'signed_in'; session: SessionKind };

export type PendingRevocation = {
  token: string;
  idempotencyKey: string;
};

let state: AuthState = { status: 'signed_out' };
let queryClient = createQueryClient();

async function randomIdempotencyKey(): Promise<string> {
  const bytes = await Crypto.getRandomBytesAsync(16);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join(
    '',
  );
}

async function readSession(): Promise<SessionKind | null> {
  const raw = await secureGet(AUTH_SESSION_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as SessionKind;
    if (parsed.kind === 'ordinary') {
      return { kind: 'ordinary' };
    }
    if (
      parsed.kind === 'store_review' &&
      (parsed.provider === 'apple' || parsed.provider === 'google')
    ) {
      return { kind: 'store_review', provider: parsed.provider };
    }
    return null;
  } catch {
    return null;
  }
}

async function writeSession(session: SessionKind): Promise<void> {
  await secureSet(AUTH_SESSION_KEY, JSON.stringify(session));
}

async function readPendingRevocation(): Promise<PendingRevocation | null> {
  const raw = await secureGet(AUTH_PENDING_REVOKE_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as PendingRevocation;
    if (
      typeof parsed.token === 'string' &&
      typeof parsed.idempotencyKey === 'string' &&
      /^[0-9A-Fa-f]{32}$/.test(parsed.idempotencyKey)
    ) {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

async function writePendingRevocation(pending: PendingRevocation): Promise<void> {
  await secureSet(AUTH_PENDING_REVOKE_KEY, JSON.stringify(pending));
}

export const AuthStore = {
  getState(): AuthState {
    return state;
  },

  getQueryClient() {
    return queryClient;
  },

  replaceQueryClient(next = createQueryClient()): void {
    queryClient.clear();
    queryClient = next;
  },

  async bootstrap(): Promise<AuthState> {
    const pending = await readPendingRevocation();
    if (pending) {
      // Do not use pending token for ordinary requests; retry revoke when online.
      state = { status: 'signed_out' };
      return state;
    }
    const token = await CredentialStore.get();
    if (!token) {
      state = { status: 'signed_out' };
      return state;
    }
    const session = (await readSession()) ?? { kind: 'ordinary' };
    state = { status: 'signed_in', session };
    return state;
  },

  async completeExchange(
    token: string,
    session: SessionKind,
  ): Promise<AuthState> {
    const pending = await readPendingRevocation();
    if (pending) {
      throw new Error(
        'Cannot sign in while a pending token revocation is outstanding.',
      );
    }
    await CredentialStore.set(token);
    await writeSession(session);
    state = { status: 'signed_in', session };
    return state;
  },

  async handleUnauthorized(): Promise<void> {
    await CredentialStore.clear();
    await secureDelete(AUTH_SESSION_KEY);
    await PrivateCache.clearAll();
    queryClient.clear();
    state = { status: 'signed_out' };
  },

  /**
   * Move bearer into pending-revocation, clear private state, retry sign-out until 204.
   */
  async signOut(options: {
    discardLocalWork?: boolean;
    hasStagedUpload?: boolean;
  } = {}): Promise<'signed_out' | 'cancelled'> {
    if (options.hasStagedUpload && !options.discardLocalWork) {
      return 'cancelled';
    }

    const token = await CredentialStore.get();
    if (!token) {
      state = { status: 'signed_out' };
      return 'signed_out';
    }

    const idempotencyKey = await randomIdempotencyKey();
    await writePendingRevocation({ token, idempotencyKey });
    await CredentialStore.clear();
    await secureDelete(AUTH_SESSION_KEY);
    await PrivateCache.clearAll();
    queryClient.clear();
    state = { status: 'signed_out' };

    await AuthStore.retryPendingRevocation();
    return 'signed_out';
  },

  async retryPendingRevocation(): Promise<'cleared' | 'pending' | 'none'> {
    const pending = await readPendingRevocation();
    if (!pending) {
      return 'none';
    }
    try {
      await apiRequest<void>('/api/v1/auth/sign-out', {
        method: 'POST',
        idempotencyKey: pending.idempotencyKey,
        bearerOverride: pending.token,
        authenticated: true,
      });
      await secureDelete(AUTH_PENDING_REVOKE_KEY);
      return 'cleared';
    } catch {
      return 'pending';
    }
  },
};

export function wireAuthApiClient(
  env: AppEnvironment,
  identity: AppIdentityHeaders,
  fetchImpl?: typeof fetch,
): void {
  resetApiClient();
  configureApiClient({
    baseUrl: env.apiBaseUrl.href,
    identity,
    fetchImpl,
    getBearer: () => CredentialStore.get(),
    onUnauthorized: () => AuthStore.handleUnauthorized(),
  });
}
