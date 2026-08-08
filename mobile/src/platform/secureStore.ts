import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * Closed inventory of SecureStore namespaces that EnvironmentBoundary must purge.
 * Adding a durable secret without registering it here must fail tests.
 */
export const SECURE_STORE_PURGE_KEYS = [
  'ci.auth.token.v1',
  'ci.auth.session.v1',
  'ci.auth.pending_revoke.v1',
  'ci.install.id.v1',
  'ci.auth.pkce.pending.v1',
  'ci.auth.stepup.pending.v1',
  'ci.privacy.replay.v1',
  'ci.purchase.idempotency.v1',
  'ci.upload.idempotency.v1',
  'ci.practice.idempotency.v1',
  'ci.telemetry.idempotency.v1',
] as const;

export type SecureStorePurgeKey = (typeof SECURE_STORE_PURGE_KEYS)[number];

export const AUTH_TOKEN_KEY = 'ci.auth.token.v1' as const;
export const AUTH_SESSION_KEY = 'ci.auth.session.v1' as const;
export const AUTH_PENDING_REVOKE_KEY = 'ci.auth.pending_revoke.v1' as const;
export const INSTALLATION_ID_KEY = 'ci.install.id.v1' as const;
export const ENV_MARKER_KEY = 'ci.env.marker.v1' as const;
export const ENV_PURGE_JOURNAL_KEY = 'ci.env.purge_journal.v1' as const;

const IOS_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

function optionsForPlatform(): SecureStore.SecureStoreOptions | undefined {
  return Platform.OS === 'ios' ? IOS_OPTIONS : undefined;
}

export type SecureStoreAdapter = {
  getItemAsync: (key: string) => Promise<string | null>;
  setItemAsync: (key: string, value: string) => Promise<void>;
  deleteItemAsync: (key: string) => Promise<void>;
};

const defaultAdapter: SecureStoreAdapter = {
  getItemAsync: (key) => SecureStore.getItemAsync(key, optionsForPlatform()),
  setItemAsync: (key, value) =>
    SecureStore.setItemAsync(key, value, optionsForPlatform()),
  deleteItemAsync: (key) => SecureStore.deleteItemAsync(key, optionsForPlatform()),
};

let adapter: SecureStoreAdapter = defaultAdapter;

/** Test seam — restore with resetSecureStoreAdapter(). */
export function setSecureStoreAdapter(next: SecureStoreAdapter): void {
  adapter = next;
}

export function resetSecureStoreAdapter(): void {
  adapter = defaultAdapter;
}

export const CredentialStore = {
  async get(): Promise<string | null> {
    return adapter.getItemAsync(AUTH_TOKEN_KEY);
  },

  async set(token: string): Promise<void> {
    if (!token || token.includes('\n') || token.includes('\0')) {
      throw new Error('Refusing to persist a malformed bearer token.');
    }
    await adapter.setItemAsync(AUTH_TOKEN_KEY, token);
  },

  async clear(): Promise<void> {
    await adapter.deleteItemAsync(AUTH_TOKEN_KEY);
  },
};

export async function secureGet(key: string): Promise<string | null> {
  return adapter.getItemAsync(key);
}

export async function secureSet(key: string, value: string): Promise<void> {
  await adapter.setItemAsync(key, value);
}

export async function secureDelete(key: string): Promise<void> {
  await adapter.deleteItemAsync(key);
}

export async function purgeRegisteredSecureStoreKeys(): Promise<void> {
  for (const key of SECURE_STORE_PURGE_KEYS) {
    await adapter.deleteItemAsync(key);
  }
}

/** In-memory SecureStore for Jest. */
export function createMemorySecureStoreAdapter(): SecureStoreAdapter & {
  store: Map<string, string>;
} {
  const store = new Map<string, string>();
  return {
    store,
    async getItemAsync(key) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    async setItemAsync(key, value) {
      store.set(key, value);
    },
    async deleteItemAsync(key) {
      store.delete(key);
    },
  };
}
