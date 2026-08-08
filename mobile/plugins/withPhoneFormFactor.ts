import {
  AndroidConfig,
  ConfigPlugin,
  withAndroidManifest,
} from '@expo/config-plugins';

/**
 * Phone-oriented Android form-factor declaration.
 * Emits supports-screens without deprecated resizeable or invented width limits.
 * Source config is provisional; signed AAB + Play Device Catalog readback is authoritative.
 */
export const PHONE_SUPPORTS_SCREENS = {
  smallScreens: true,
  normalScreens: true,
  largeScreens: false,
  xlargeScreens: false,
  anyDensity: true,
} as const;

const withPhoneFormFactor: ConfigPlugin = (config) => {
  return withAndroidManifest(config, (cfg) => {
    const manifest = cfg.modResults;
    const application = AndroidConfig.Manifest.getMainApplicationOrThrow(manifest);

    // Ensure manifest has supports-screens on the root <manifest>.
    const root = manifest.manifest;
    if (!root['supports-screens']) {
      root['supports-screens'] = [];
    }

    const screens = {
      $: {
        'android:smallScreens': String(PHONE_SUPPORTS_SCREENS.smallScreens),
        'android:normalScreens': String(PHONE_SUPPORTS_SCREENS.normalScreens),
        'android:largeScreens': String(PHONE_SUPPORTS_SCREENS.largeScreens),
        'android:xlargeScreens': String(PHONE_SUPPORTS_SCREENS.xlargeScreens),
        'android:anyDensity': String(PHONE_SUPPORTS_SCREENS.anyDensity),
      },
    };

    root['supports-screens'] = [screens];

    // Keep application present; no TV/Wear/Automotive/XR feature requirements.
    const usesFeature = root['uses-feature'] ?? [];
    const banned = [
      'android.software.leanback',
      'android.hardware.type.watch',
      'android.hardware.type.automotive',
      'android.software.xr.api.openxr',
    ];
    root['uses-feature'] = usesFeature.filter((entry) => {
      const name = entry?.$?.['android:name'];
      return !name || !banned.includes(name);
    });

    // Touch application so the mod is considered applied.
    void application;
    return cfg;
  });
};

export default withPhoneFormFactor;
