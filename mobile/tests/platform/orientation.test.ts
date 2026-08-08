const lockAsync = jest.fn(async () => undefined);
const unlockAsync = jest.fn(async () => undefined);

jest.mock('expo-screen-orientation', () => ({
  OrientationLock: {
    PORTRAIT_UP: 'PORTRAIT_UP',
  },
  lockAsync: (...args: unknown[]) => lockAsync(...args),
  unlockAsync: (...args: unknown[]) => unlockAsync(...args),
}));

import { OrientationController } from '../../src/platform/orientation';

describe('OrientationController', () => {
  beforeEach(() => {
    lockAsync.mockClear();
    unlockAsync.mockClear();
  });

  it('enterCapture unlocks rotation for capture', async () => {
    await OrientationController.enterCapture();
    expect(unlockAsync).toHaveBeenCalledTimes(1);
  });

  it('leaveCapture restores portrait-up', async () => {
    await OrientationController.leaveCapture();
    expect(lockAsync).toHaveBeenCalledWith('PORTRAIT_UP');
  });
});
