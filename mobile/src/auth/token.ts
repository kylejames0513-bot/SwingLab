/**
 * Device-token storage.
 *
 * The credential is issued once by an authenticated browser session and
 * returned exactly once, in a no-store response. It goes straight to the
 * platform keychain and is never written anywhere else — not to AsyncStorage,
 * not to a log line, not into a crash report.
 *
 * See docs/mobile-api-tokens.md for issuance, the five-active-token cap, the
 * 90-day expiry, and the auth-epoch binding.
 */

import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "caddieinsight.device_token";

/** Format is `ciat_<selector>.<secret>`. Checking the shape locally lets a
 *  paste error fail immediately with a clear message rather than as a 401
 *  three screens later. */
const TOKEN_PATTERN = /^ciat_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function looksLikeDeviceToken(value: string): boolean {
  return TOKEN_PATTERN.test(value.trim());
}

export async function readToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(TOKEN_KEY);
  } catch {
    // A keychain read can fail on a locked device. Report "no token" rather
    // than crashing; the caller routes to the connect screen.
    return null;
  }
}

export async function saveToken(token: string): Promise<void> {
  const trimmed = token.trim();
  if (!looksLikeDeviceToken(trimmed)) {
    throw new Error("That does not look like a CaddieInsight device token.");
  }
  await SecureStore.setItemAsync(TOKEN_KEY, trimmed, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export async function clearToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    // Already gone is the desired end state.
  }
}
