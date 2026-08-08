import { Platform } from 'react-native';

import { PrivateNoBackupStorage } from '../../src/platform/privateNoBackupStorage';

describe('PrivateNoBackupStorage', () => {
  const originalOS = Platform.OS;

  afterEach(() => {
    Object.defineProperty(Platform, 'OS', {
      configurable: true,
      get: () => originalOS,
    });
  });

  it('fail-closes on web / unprotected platforms', async () => {
    Object.defineProperty(Platform, 'OS', {
      configurable: true,
      get: () => 'web',
    });
    await expect(PrivateNoBackupStorage.ensureRoots()).rejects.toThrow(
      /fail closed|native|unprotected|requires/i,
    );
  });

  it('reject empty protectAndVerify URIs on native OS before native call shape', async () => {
    Object.defineProperty(Platform, 'OS', {
      configurable: true,
      get: () => 'ios',
    });
    await expect(PrivateNoBackupStorage.protectAndVerify('')).rejects.toThrow(
      /non-empty/i,
    );
  });

  it('fail-closes when native module is not linked on ios', async () => {
    Object.defineProperty(Platform, 'OS', {
      configurable: true,
      get: () => 'ios',
    });
    await expect(PrivateNoBackupStorage.ensureRoots()).rejects.toThrow(
      /not linked|not implemented|Unavailable|fail closed|native/i,
    );
  });
});
