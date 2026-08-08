import { AuthStore } from '@/auth/authStore';
import { PrivateCache } from '@/platform/privateCache';
import { flushQueuedPracticeEvidence } from '@/features/practice/api';
import { uploadRepository } from '@/features/analysis/uploadRepository';
import { refreshPushRegistration } from '@/platform/notifications';
import Constants from 'expo-constants';

/**
 * Foreground / cold-start reconciliation for private current truth.
 */
export async function reconcileOnForeground(): Promise<void> {
  if (AuthStore.getState().status !== 'signed_in') {
    await AuthStore.retryPendingRevocation();
    return;
  }
  await AuthStore.retryPendingRevocation();
  await flushQueuedPracticeEvidence();
  const pending = await uploadRepository.load();
  if (pending && (pending.state === 'paused' || pending.state === 'uploading')) {
    // UploadScreen owns resume UX; leave durable state intact.
  }
  await refreshPushRegistration(Constants.expoConfig?.version ?? '1.0.0').catch(
    () => undefined,
  );
}

export async function purgePrivateClientState(): Promise<void> {
  AuthStore.getQueryClient().clear();
  await PrivateCache.clearAll();
  await uploadRepository.clear();
}
