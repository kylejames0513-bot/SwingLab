import { PrivateNoBackupStorage } from '@/platform/privateNoBackupStorage';

export type PrivateCacheRoots = {
  accountId: string;
  stateDirectory: string;
};

type CacheBackend = {
  clearAccount: (accountId: string) => Promise<void>;
  clearAll: () => Promise<void>;
  writeJson: (accountId: string, name: string, value: unknown) => Promise<void>;
  readJson: <T>(accountId: string, name: string) => Promise<T | null>;
};

const memory = new Map<string, Map<string, string>>();

const memoryBackend: CacheBackend = {
  async clearAccount(accountId) {
    memory.delete(accountId);
  },
  async clearAll() {
    memory.clear();
  },
  async writeJson(accountId, name, value) {
    let bucket = memory.get(accountId);
    if (!bucket) {
      bucket = new Map();
      memory.set(accountId, bucket);
    }
    bucket.set(name, JSON.stringify(value));
  },
  async readJson<T>(accountId: string, name: string) {
    const raw = memory.get(accountId)?.get(name);
    if (raw == null) {
      return null;
    }
    return JSON.parse(raw) as T;
  },
};

let backend: CacheBackend = memoryBackend;
let activeAccountId: string | null = null;

/** Test seam. */
export function setPrivateCacheBackend(next: CacheBackend): void {
  backend = next;
}

export function resetPrivateCacheBackend(): void {
  backend = memoryBackend;
  memory.clear();
  activeAccountId = null;
}

/**
 * Account-scoped private cache under backup-excluded storage.
 * Native atomic rename + protectAndVerify land when the storage module is real;
 * Jest uses an in-memory backend.
 */
export const PrivateCache = {
  async setActiveAccount(accountId: string): Promise<void> {
    if (!accountId) {
      throw new Error('PrivateCache requires a non-empty account id.');
    }
    activeAccountId = accountId;
    // Ensure roots exist on native; fail closed on unsupported platforms only
    // when a feature actually needs filesystem paths (uploads/exports).
    try {
      await PrivateNoBackupStorage.stateDirectory();
    } catch {
      // Memory backend remains usable in tests / web fail-closed for media.
    }
  },

  getActiveAccountId(): string | null {
    return activeAccountId;
  },

  async writeJson(name: string, value: unknown): Promise<void> {
    if (!activeAccountId) {
      throw new Error('PrivateCache has no active account.');
    }
    await backend.writeJson(activeAccountId, name, value);
  },

  async readJson<T>(name: string): Promise<T | null> {
    if (!activeAccountId) {
      return null;
    }
    return backend.readJson<T>(activeAccountId, name);
  },

  async clearAll(): Promise<void> {
    await backend.clearAll();
    activeAccountId = null;
  },

  async clearActiveAccount(): Promise<void> {
    if (activeAccountId) {
      await backend.clearAccount(activeAccountId);
    }
    activeAccountId = null;
  },
};
