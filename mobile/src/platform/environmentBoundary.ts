import type { AppEnvironment } from '@/config/env';
import {
  ENV_MARKER_KEY,
  ENV_PURGE_JOURNAL_KEY,
  purgeRegisteredSecureStoreKeys,
  secureDelete,
  secureGet,
  secureSet,
  CredentialStore,
} from '@/platform/secureStore';
import { PrivateCache } from '@/platform/privateCache';
import { resetApiClient } from '@/api/client';

export type EnvironmentBoundaryResult = 'ready';

export type EnvironmentMarker = {
  environmentIdentity: string;
  apiOrigin: string;
};

export type PurgeJournal = {
  phase:
    | 'started'
    | 'credentials_cleared'
    | 'cache_cleared'
    | 'verified';
  targetIdentity: string;
  targetOrigin: string;
};

type QueryClientLike = {
  clear: () => void;
};

let gateReady = false;
let queryClientRef: QueryClientLike | null = null;
const purgeHooks: (() => Promise<void> | void)[] = [];

export function registerEnvironmentPurgeHook(
  hook: () => Promise<void> | void,
): () => void {
  purgeHooks.push(hook);
  return () => {
    const index = purgeHooks.indexOf(hook);
    if (index >= 0) {
      purgeHooks.splice(index, 1);
    }
  };
}

export function setEnvironmentQueryClient(client: QueryClientLike | null): void {
  queryClientRef = client;
}

export function isEnvironmentBoundaryReady(): boolean {
  return gateReady;
}

export function resetEnvironmentBoundaryForTests(): void {
  gateReady = false;
  queryClientRef = null;
  purgeHooks.length = 0;
}

async function readMarker(): Promise<EnvironmentMarker | null> {
  const raw = await secureGet(ENV_MARKER_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as EnvironmentMarker;
    if (
      typeof parsed.environmentIdentity !== 'string' ||
      typeof parsed.apiOrigin !== 'string'
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

async function readJournal(): Promise<PurgeJournal | null> {
  const raw = await secureGet(ENV_PURGE_JOURNAL_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as PurgeJournal;
  } catch {
    return null;
  }
}

async function writeJournal(journal: PurgeJournal): Promise<void> {
  await secureSet(ENV_PURGE_JOURNAL_KEY, JSON.stringify(journal));
}

async function runPurge(target: EnvironmentMarker): Promise<void> {
  await writeJournal({
    phase: 'started',
    targetIdentity: target.environmentIdentity,
    targetOrigin: target.apiOrigin,
  });

  await purgeRegisteredSecureStoreKeys();
  await writeJournal({
    phase: 'credentials_cleared',
    targetIdentity: target.environmentIdentity,
    targetOrigin: target.apiOrigin,
  });

  await PrivateCache.clearAll();
  queryClientRef?.clear();
  resetApiClient();
  for (const hook of [...purgeHooks]) {
    await hook();
  }

  await writeJournal({
    phase: 'cache_cleared',
    targetIdentity: target.environmentIdentity,
    targetOrigin: target.apiOrigin,
  });

  const token = await CredentialStore.get();
  if (token) {
    throw new Error('Environment purge failed: credential still present.');
  }

  await writeJournal({
    phase: 'verified',
    targetIdentity: target.environmentIdentity,
    targetOrigin: target.apiOrigin,
  });

  // Marker is written last; journal cleared only after marker persists.
  await secureSet(
    ENV_MARKER_KEY,
    JSON.stringify({
      environmentIdentity: target.environmentIdentity,
      apiOrigin: target.apiOrigin,
    } satisfies EnvironmentMarker),
  );
  await secureDelete(ENV_PURGE_JOURNAL_KEY);
}

/**
 * First root-app operation and sole gate to SecureStore/private-cache reads,
 * API construction, deep-link handling, or private rendering.
 */
export const EnvironmentBoundary = {
  async bootstrap(appEnvironment: AppEnvironment): Promise<EnvironmentBoundaryResult> {
    gateReady = false;
    const target: EnvironmentMarker = {
      environmentIdentity: appEnvironment.environmentIdentity,
      apiOrigin: appEnvironment.apiOrigin,
    };

    const journal = await readJournal();
    if (journal) {
      await runPurge({
        environmentIdentity: journal.targetIdentity,
        apiOrigin: journal.targetOrigin,
      });
    }

    const marker = await readMarker();
    const mismatch =
      !marker ||
      marker.environmentIdentity !== target.environmentIdentity ||
      marker.apiOrigin !== target.apiOrigin;

    if (mismatch) {
      await runPurge(target);
    }

    gateReady = true;
    return 'ready';
  },

  assertReady(): void {
    if (!gateReady) {
      throw new Error(
        'EnvironmentBoundary is not ready; refusing private SecureStore/API access.',
      );
    }
  },
};
