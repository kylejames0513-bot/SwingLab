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
  digestStringAsync: jest.fn(async () => 'abcdef0123456789abcdef0123456789ffff'),
  getRandomBytesAsync: jest.fn(async (size: number) =>
    Uint8Array.from({ length: size }, (_, i) => i + 1),
  ),
}));

// Default process env for modules that read it at import time.
process.env.EXPO_PUBLIC_APP_ENV =
  process.env.EXPO_PUBLIC_APP_ENV ?? 'development';
process.env.EXPO_PUBLIC_API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? 'https://api.example.com';
