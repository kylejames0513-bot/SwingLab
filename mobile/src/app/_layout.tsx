import { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import { QueryClientProvider } from '@tanstack/react-query';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { AppState, Platform, StyleSheet, Text, View } from 'react-native';
import * as Application from 'expo-application';

import { AuthStore, wireAuthApiClient } from '@/auth/authStore';
import { deriveAppIdentityHeaders } from '@/config/appIdentity';
import {
  getAppEnvironment,
  type AppEnvironment,
} from '@/config/env';
import { ThemeProvider } from '@/design/theme';
import {
  EnvironmentBoundary,
  setEnvironmentQueryClient,
} from '@/platform/environmentBoundary';
import { OrientationController } from '@/platform/orientation';
import { useDeepLinkRouter } from '@/platform/useDeepLinkRouter';
import { reconcileOnForeground } from '@/platform/reliability';

SplashScreen.preventAutoHideAsync().catch(() => {
  // Splash may already be hidden in tests / web.
});

type BootState =
  | { status: 'loading' }
  | { status: 'ready'; env: AppEnvironment }
  | { status: 'error'; message: string };

export default function RootLayout() {
  const [boot, setBoot] = useState<BootState>({ status: 'loading' });
  useDeepLinkRouter(boot.status === 'ready');

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await OrientationController.leaveCapture();
        const env = getAppEnvironment();
        setEnvironmentQueryClient(AuthStore.getQueryClient());
        await EnvironmentBoundary.bootstrap(env);
        EnvironmentBoundary.assertReady();

        if (Platform.OS === 'ios' || Platform.OS === 'android') {
          const identity = deriveAppIdentityHeaders({
            environment: env,
            platform: Platform.OS,
            appVersion: Application.nativeApplicationVersion ?? '1.0.0',
            appBuild: Application.nativeBuildVersion ?? '1',
            applicationId:
              Application.applicationId ??
              (env.environment === 'production'
                ? 'com.caddieinsight.app'
                : env.environment === 'staging'
                  ? 'com.caddieinsight.app.staging'
                  : 'com.caddieinsight.app.dev'),
          });
          wireAuthApiClient(env, identity);
          await AuthStore.bootstrap();
          await AuthStore.retryPendingRevocation();
        }

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

  useEffect(() => {
    if (boot.status !== 'ready') {
      return;
    }
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') {
        void reconcileOnForeground();
      }
    });
    return () => sub.remove();
  }, [boot.status]);

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
    <QueryClientProvider client={AuthStore.getQueryClient()}>
      <ThemeProvider>
        <Stack screenOptions={{ headerShown: false }} />
        <StatusBar style="dark" />
      </ThemeProvider>
    </QueryClientProvider>
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
