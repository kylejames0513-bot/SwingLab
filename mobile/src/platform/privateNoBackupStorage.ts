import { Platform } from 'react-native';

import CaddieInsightStorage from '../../modules/caddieinsight-storage';

export type PrivateStorageRoots = {
  pendingUploadsDirectory: string;
  stateDirectory: string;
  exportTemporaryDirectory: string;
};

const UNSUPPORTED =
  'PrivateNoBackupStorage requires the native caddieinsight-storage module on iOS/Android. Web and unprotected platforms fail closed.';

function assertNativePlatform(): void {
  if (Platform.OS !== 'ios' && Platform.OS !== 'android') {
    throw new Error(UNSUPPORTED);
  }
}

/**
 * App-private directories with backup exclusion / noBackupFilesDir.
 * Fail-closed when the native module is unavailable (web, Jest without mocks).
 */
export const PrivateNoBackupStorage = {
  async ensureRoots(): Promise<PrivateStorageRoots> {
    assertNativePlatform();
    return CaddieInsightStorage.ensureProtectedRoots();
  },

  async pendingUploadsDirectory(): Promise<string> {
    const roots = await this.ensureRoots();
    return roots.pendingUploadsDirectory;
  },

  async stateDirectory(): Promise<string> {
    const roots = await this.ensureRoots();
    return roots.stateDirectory;
  },

  async exportTemporaryDirectory(): Promise<string> {
    const roots = await this.ensureRoots();
    return roots.exportTemporaryDirectory;
  },

  async protectAndVerify(uri: string): Promise<void> {
    assertNativePlatform();
    if (!uri || typeof uri !== 'string') {
      throw new Error('protectAndVerify requires a non-empty file URI.');
    }
    await CaddieInsightStorage.protectAndVerify(uri);
  },
};
