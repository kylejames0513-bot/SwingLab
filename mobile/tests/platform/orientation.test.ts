const mockLockAsync = jest.fn(async (_lock?: string) => undefined);
const mockUnlockAsync = jest.fn(async () => undefined);

jest.mock('expo-screen-orientation', () => ({
  OrientationLock: {
    PORTRAIT_UP: 'PORTRAIT_UP',
  },
  lockAsync: (lock: string) => mockLockAsync(lock),
  unlockAsync: () => mockUnlockAsync(),
}));

import { OrientationController } from '../../src/platform/orientation';

describe('OrientationController', () => {
  beforeEach(() => {
    mockLockAsync.mockClear();
    mockUnlockAsync.mockClear();
  });

  it('enterCapture unlocks rotation for capture', async () => {
    await OrientationController.enterCapture();
    expect(mockUnlockAsync).toHaveBeenCalledTimes(1);
  });

  it('leaveCapture restores portrait-up', async () => {
    await OrientationController.leaveCapture();
    expect(mockLockAsync).toHaveBeenCalledWith('PORTRAIT_UP');
  });
});
