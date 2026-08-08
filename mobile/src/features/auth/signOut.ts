import { AuthStore } from '@/auth/authStore';
import { PrivateCache } from '@/platform/privateCache';

export type SignOutPrompt =
  | { kind: 'confirm' }
  | { kind: 'staged_upload' };

export function signOutPrompt(hasStagedUpload: boolean): SignOutPrompt {
  return hasStagedUpload ? { kind: 'staged_upload' } : { kind: 'confirm' };
}

export async function confirmSignOut(options: {
  hasStagedUpload: boolean;
  discardLocalWork: boolean;
}): Promise<'signed_out' | 'cancelled' | 'pending_revoke'> {
  const result = await AuthStore.signOut({
    hasStagedUpload: options.hasStagedUpload,
    discardLocalWork: options.discardLocalWork,
  });
  if (result === 'cancelled') {
    return 'cancelled';
  }
  await PrivateCache.clearAll();
  const revoke = await AuthStore.retryPendingRevocation();
  return revoke === 'pending' ? 'pending_revoke' : 'signed_out';
}
