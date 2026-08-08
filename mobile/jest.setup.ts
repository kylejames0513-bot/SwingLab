// Jest setup for CaddieInsight mobile tests.
jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(async () => undefined),
  hideAsync: jest.fn(async () => undefined),
}));

jest.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

jest.mock('expo-crypto', () => ({
  CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
  CryptoEncoding: { HEX: 'hex', BASE64: 'base64' },
  digestStringAsync: jest.fn(async (_alg: string, data: string, options?: { encoding?: string }) => {
    // Deterministic stand-in: produce a 44-char base64-ish digest for PKCE tests.
    if (options?.encoding === 'base64') {
      return 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=';
    }
    return 'abcdef0123456789abcdef0123456789ffffabcdef0123456789abcdef01234567';
  }),
  getRandomBytesAsync: jest.fn(async (size: number) =>
    Uint8Array.from({ length: size }, (_, i) => (i % 255) + 1),
  ),
}));

jest.mock('@preeternal/react-native-file-hash', () => ({
  fileHash: jest.fn(async () => 'a'.repeat(64)),
}));

jest.mock('expo-haptics', () => ({
  ImpactFeedbackStyle: { Medium: 'medium' },
  impactAsync: jest.fn(async () => undefined),
}));

jest.mock('expo-camera', () => {
  const React = require('react');
  return {
    CameraView: React.forwardRef(() => null),
    useCameraPermissions: () => [{ granted: true }, jest.fn(async () => ({ granted: true }))],
    useMicrophonePermissions: () => [
      { granted: true },
      jest.fn(async () => ({ granted: true })),
    ],
  };
});

jest.mock('expo-image-picker', () => ({
  VideoExportPreset: { Passthrough: 0 },
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
}));

jest.mock('expo-video', () => {
  const React = require('react');
  return {
    useVideoPlayer: () => ({}),
    VideoView: () => null,
  };
});

// PKCE helpers use btoa; provide a minimal polyfill for Jest.
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (value: string) =>
    Buffer.from(value, 'binary').toString('base64');
}

// Default process env for modules that read it at import time.
process.env.EXPO_PUBLIC_APP_ENV =
  process.env.EXPO_PUBLIC_APP_ENV ?? 'development';
process.env.EXPO_PUBLIC_API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? 'https://api.example.com';
