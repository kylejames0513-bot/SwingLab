import * as ScreenOrientation from 'expo-screen-orientation';

/**
 * Runtime orientation controller: portrait-up outside capture;
 * unlock portrait/landscape while recording a swing.
 */
export const OrientationController = {
  async enterCapture(): Promise<void> {
    await ScreenOrientation.unlockAsync();
  },

  async leaveCapture(): Promise<void> {
    await ScreenOrientation.lockAsync(
      ScreenOrientation.OrientationLock.PORTRAIT_UP,
    );
  },
};
