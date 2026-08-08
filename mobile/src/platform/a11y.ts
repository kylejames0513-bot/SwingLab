/**
 * Accessibility helpers shared by screens.
 * Every interactive control should meet platform minimum targets.
 */
export const MIN_TOUCH_IOS = 44;
export const MIN_TOUCH_ANDROID = 48;

export function minTouchSize(platform: 'ios' | 'android' | string): number {
  return platform === 'ios' ? MIN_TOUCH_IOS : MIN_TOUCH_ANDROID;
}

export type ReducedMotionPreference = boolean;

export function shouldReduceMotion(prefersReducedMotion: boolean): boolean {
  return prefersReducedMotion;
}
