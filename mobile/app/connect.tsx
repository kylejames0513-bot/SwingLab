import { useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { apiBaseUrl } from "@/api/client";
import { saveToken } from "@/auth/token";

/**
 * Connecting a device.
 *
 * A device token can only be minted by an authenticated same-origin browser
 * session — a bearer credential cannot mint one, by design. So this screen
 * cannot do the issuing itself: it opens the account page, the golfer creates
 * a token there, and pastes it back. That paste step is the deliberate cost of
 * not letting the app hold credentials that could mint more credentials.
 */
export default function ConnectScreen() {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const queryClient = useQueryClient();

  async function openAccount() {
    const base = apiBaseUrl();
    if (!base) {
      setError("No API base URL is configured for this build.");
      return;
    }
    await WebBrowser.openBrowserAsync(`${base}/account`);
  }

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      await saveToken(value);
      // Anything cached belongs to the previous credential, which may have
      // been a different account entirely.
      queryClient.clear();
      router.replace("/");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Could not save that token.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Connect this device</Text>
      <Text style={styles.body}>
        Device tokens are issued from your account page in a browser, where
        you are already signed in. Create one there, then paste it here. It is
        shown only once.
      </Text>

      <Pressable style={styles.secondary} onPress={openAccount}>
        <Text style={styles.secondaryLabel}>Open account page</Text>
      </Pressable>

      <TextInput
        style={styles.input}
        value={value}
        onChangeText={setValue}
        placeholder="ciat_…"
        placeholderTextColor="#5a655e"
        autoCapitalize="none"
        autoCorrect={false}
        // The credential must never reach a keyboard learning dictionary or a
        // predictive-text cache.
        autoComplete="off"
        textContentType="password"
        secureTextEntry
        accessibilityLabel="Device token"
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable
        style={[styles.primary, (busy || !value) && styles.disabled]}
        disabled={busy || !value}
        onPress={connect}
      >
        <Text style={styles.primaryLabel}>
          {busy ? "Connecting…" : "Connect"}
        </Text>
      </Pressable>

      <Text style={styles.footnote}>
        Tokens expire after 90 days and can be revoked from the same account
        page. Up to five devices can be connected at once.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#eef2ef" },
  content: { padding: 22, gap: 14 },
  title: { color: "#0f3d28", fontSize: 28, fontWeight: "800", letterSpacing: -0.9 },
  body: { color: "#445049", fontSize: 15, lineHeight: 22 },
  input: {
    minHeight: 48,
    paddingHorizontal: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#6f7b72",
    backgroundColor: "#ffffff",
    color: "#101a14",
    fontSize: 16,
  },
  primary: {
    minHeight: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 12,
    backgroundColor: "#0f3d28",
  },
  primaryLabel: { color: "#e6f2ea", fontSize: 16, fontWeight: "700" },
  secondary: {
    minHeight: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#0f3d28",
  },
  secondaryLabel: { color: "#0f3d28", fontSize: 16, fontWeight: "700" },
  disabled: { opacity: 0.5 },
  error: { color: "#8f4509", fontSize: 14 },
  footnote: { color: "#5a655e", fontSize: 13, lineHeight: 19 },
});
