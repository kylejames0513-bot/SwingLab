import type { ConfigContext, ExpoConfig } from 'expo/config';

type AppEnvName = 'development' | 'staging' | 'production';

function resolveAppEnv(): AppEnvName {
  const raw = (process.env.EXPO_PUBLIC_APP_ENV ?? 'development')
    .trim()
    .toLowerCase();
  if (raw === 'staging' || raw === 'production' || raw === 'development') {
    return raw;
  }
  return 'development';
}

function bundleIdentifier(env: AppEnvName): string {
  if (env === 'development') return 'com.caddieinsight.app.dev';
  if (env === 'staging') return 'com.caddieinsight.app.staging';
  return 'com.caddieinsight.app';
}

function schemeFor(env: AppEnvName): string {
  if (env === 'development') return 'caddieinsight-dev';
  if (env === 'staging') return 'caddieinsight-staging';
  return 'caddieinsight';
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const appEnv = resolveAppEnv();
  const bundleId = bundleIdentifier(appEnv);

  return {
    ...config,
    name: 'CaddieInsight',
    slug: 'caddieinsight',
    version: '1.0.0',
    orientation: 'default',
    scheme: schemeFor(appEnv),
    userInterfaceStyle: 'automatic',
    newArchEnabled: true,
    icon: './assets/icon.png',
    splash: {
      image: './assets/splash-icon.png',
      resizeMode: 'contain',
      backgroundColor: '#1A3D2E',
    },
    ios: {
      supportsTablet: false,
      bundleIdentifier: bundleId,
      infoPlist: {
        UISupportedInterfaceOrientations: [
          'UIInterfaceOrientationPortrait',
          'UIInterfaceOrientationLandscapeLeft',
          'UIInterfaceOrientationLandscapeRight',
        ],
        NSCameraUsageDescription:
          'CaddieInsight uses the camera to record guided swing videos for coaching analysis.',
        NSMicrophoneUsageDescription:
          'CaddieInsight records microphone audio during guided swing capture because impact sound is part of the analysis signal.',
      },
    },
    android: {
      package: bundleId,
      adaptiveIcon: {
        foregroundImage: './assets/adaptive-icon.png',
        monochromeImage: './assets/monochrome-icon.png',
        backgroundColor: '#1A3D2E',
      },
      permissions: ['CAMERA', 'RECORD_AUDIO'],
    },
    web: {
      output: 'static',
      favicon: './assets/icon.png',
    },
    notification: {
      icon: './assets/notification-icon.png',
      color: '#1A3D2E',
    },
    plugins: [
      'expo-router',
      [
        'expo-splash-screen',
        {
          backgroundColor: '#1A3D2E',
          image: './assets/splash-icon.png',
          imageWidth: 200,
        },
      ],
      [
        'expo-build-properties',
        {
          ios: {
            deploymentTarget: '16.4',
          },
          android: {
            minSdkVersion: 24,
          },
        },
      ],
      [
        'expo-camera',
        {
          cameraPermission:
            'CaddieInsight uses the camera to record guided swing videos for coaching analysis.',
          microphonePermission:
            'CaddieInsight records microphone audio during guided swing capture because impact sound is part of the analysis signal.',
          recordAudioAndroid: true,
          barcodeScannerEnabled: false,
        },
      ],
      'expo-secure-store',
      'expo-video',
      'expo-web-browser',
      'expo-sharing',
      'expo-audio',
      'expo-screen-orientation',
      '@preeternal/react-native-file-hash',
      './plugins/withPrivacyManifest',
      './plugins/withPhoneFormFactor',
    ],
    experiments: {
      typedRoutes: true,
      reactCompiler: true,
    },
    extra: {
      appEnv,
      eas: {
        projectId: process.env.EXPO_PUBLIC_EAS_PROJECT_ID ?? undefined,
      },
    },
  };
};
