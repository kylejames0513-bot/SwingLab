import { PrivateCache } from '@/platform/privateCache';
import type { PendingUpload } from './uploadTypes';

const KEY = 'pending_upload';

export const uploadRepository = {
  async save(upload: PendingUpload): Promise<void> {
    if (PrivateCache.getActiveAccountId() !== upload.accountId) {
      await PrivateCache.setActiveAccount(upload.accountId);
    }
    await PrivateCache.writeJson(KEY, upload);
  },

  async load(): Promise<PendingUpload | null> {
    return PrivateCache.readJson<PendingUpload>(KEY);
  },

  async clear(): Promise<void> {
    await PrivateCache.writeJson(KEY, null);
  },
};
