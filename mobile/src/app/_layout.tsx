import { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { StyleSheet, Text, View } from 'react-native';

import {
  getAppEnvironment,
  type AppEnvironment,
} from '@/config/env';
import { OrientationController } from '@/platform/orientation';

SplashScreen.preventAutoHideAsync().catch(() => {
  // Splash may already be hidden in tests / web.
});

type BootState =
  | { status: 'loading' }
  | { status: 'ready'; env: AppEnvironment }
  | { status: 'error'; message: string };

export default function RootLayout() {
  const [boot, setBoot] = useState<BootState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await OrientationController.leaveCapture();
        const env = getAppEnvironment();
        if (!cancelled) {
          setBoot({ status: 'ready', env });
        }
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'Failed to configure app.';
        if (!cancelled) {
          setBoot({ status: 'error', message });
        }
      } finally {
        SplashScreen.hideAsync().catch(() => undefined);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
      void OrientationController.leaveCapture();
    };
  }, []);

  if (boot.status === 'loading') {
    return (
      <View style={styles.shell} accessibilityLabel="CaddieInsight loading">
        <Text style={styles.brand}>CaddieInsight</Text>
        <Text style={styles.subtitle}>Loading…</Text>
        <StatusBar style="light" />
      </View>
    );
  }

  if (boot.status === 'error') {
    return (
      <View style={styles.shell} accessibilityLabel="CaddieInsight configuration error">
        <Text style={styles.brand}>CaddieInsight</Text>
        <Text style={styles.error}>{boot.message}</Text>
        <StatusBar style="light" />
      </View>
    );
  }

  return (
    <>
      <Stack screenOptions={{ headerShown: false }} />
      <StatusBar style="light" />
    </>
  );
}

const styles = StyleSheet.create({
  shell: {
    flex: 1,
    backgroundColor: '#1A3D2E',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  brand: {
    color: '#F4F7F5',
    fontSize: 28,
    fontWeight: '700',
    letterSpacing: 0.4,
  },
  subtitle: {
    marginTop: 12,
    color: '#B7C9BF',
    fontSize: 16,
  },
  error: {
    marginTop: 16,
    color: '#F2C6C2',
    fontSize: 15,
    textAlign: 'center',
    lineHeight: 22,
  },
});
