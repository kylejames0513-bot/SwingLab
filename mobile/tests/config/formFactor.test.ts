import { PHONE_SUPPORTS_SCREENS } from '../../plugins/withPhoneFormFactor';

describe('withPhoneFormFactor', () => {
  it('documents phone-only supports-screens policy', () => {
    expect(PHONE_SUPPORTS_SCREENS).toEqual({
      smallScreens: true,
      normalScreens: true,
      largeScreens: false,
      xlargeScreens: false,
      anyDensity: true,
    });
  });

  it('exports a config plugin function', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const plugin = require('../../plugins/withPhoneFormFactor').default;
    expect(typeof plugin).toBe('function');
  });

  it('notes provisional source config vs Play Device Catalog', () => {
    // This source test is provisional and cannot replace signed-AAB /
    // Play Device Catalog readback for tablet/TV/Wear/Automotive/XR exclusion.
    expect(PHONE_SUPPORTS_SCREENS.largeScreens).toBe(false);
    expect(PHONE_SUPPORTS_SCREENS.xlargeScreens).toBe(false);
  });
});

describe('iOS phone form factor (app config)', () => {
  it('sets supportsTablet false for iPhone-only device family intent', () => {
    // Dynamically load app.config — Expo evaluates it as a module.
    process.env.EXPO_PUBLIC_APP_ENV = 'production';
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const configFactory = require('../../app.config.ts').default;
    const config = configFactory({ config: {} });
    expect(config.name).toBe('CaddieInsight');
    expect(config.slug).toBe('caddieinsight');
    expect(config.ios?.supportsTablet).toBe(false);
    expect(config.ios?.bundleIdentifier).toBe('com.caddieinsight.app');
    expect(config.android?.package).toBe('com.caddieinsight.app');
    // supportsTablet=false is not a Mac/Vision Pro availability control.
    expect(config.orientation).toBe('default');
  });
});
