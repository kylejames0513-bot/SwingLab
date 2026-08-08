import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import type { components } from '@/api/schema.generated';
import { AuthStore } from '@/auth/authStore';
import { AuthCallbackScreen } from '@/features/auth/AuthCallbackScreen';
import { EmailSignInScreen } from '@/features/auth/EmailSignInScreen';
import { ReviewAccessScreen } from '@/features/auth/ReviewAccessScreen';
import { fetchMe } from '@/features/auth/api';
import { PrivateCache } from '@/platform/privateCache';

export type AuthBoundaryMode =
  | { screen: 'loading' }
  | { screen: 'email' }
  | { screen: 'review' }
  | { screen: 'callback'; challengeId: string | null; emailCode: string | null }
  | { screen: 'onboarding'; me: components['schemas']['IdentityResponse'] }
  | { screen: 'app'; me: components['schemas']['IdentityResponse'] };

type Props = {
  initialCallback?: { challengeId: string | null; emailCode: string | null } | null;
  children: (me: components['schemas']['IdentityResponse']) => React.ReactNode;
  renderOnboarding: (me: components['schemas']['IdentityResponse']) => React.ReactNode;
};

function profileComplete(
  me: components['schemas']['IdentityResponse'],
): boolean {
  return Boolean(me.profile?.is_complete);
}

export function AuthBoundary({
  initialCallback = null,
  children,
  renderOnboarding,
}: Props) {
  const [mode, setMode] = useState<AuthBoundaryMode>({ screen: 'loading' });
  const [historyEpoch, setHistoryEpoch] = useState<number | null>(null);

  const applyMe = useCallback(
    async (me: components['schemas']['IdentityResponse']) => {
      const epoch = me.identity.history_epoch;
      if (historyEpoch != null && historyEpoch !== epoch) {
        AuthStore.getQueryClient().clear();
        await PrivateCache.clearAll();
      }
      setHistoryEpoch(epoch);
      await PrivateCache.setActiveAccount(me.identity.id);
      if (!profileComplete(me)) {
        setMode({ screen: 'onboarding', me });
      } else {
        setMode({ screen: 'app', me });
      }
    },
    [historyEpoch],
  );

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      if (initialCallback) {
        setMode({
          screen: 'callback',
          challengeId: initialCallback.challengeId,
          emailCode: initialCallback.emailCode,
        });
        return;
      }
      const auth = await AuthStore.bootstrap();
      if (cancelled) {
        return;
      }
      if (auth.status !== 'signed_in') {
        setMode({ screen: 'email' });
        return;
      }
      try {
        const me = await fetchMe();
        if (!cancelled) {
          await applyMe(me);
        }
      } catch {
        if (!cancelled) {
          setMode({ screen: 'email' });
        }
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [applyMe, initialCallback]);

  if (mode.screen === 'loading') {
    return (
      <View style={styles.shell} accessibilityLabel="Checking sign-in">
        <Text style={styles.brand}>CaddieInsight</Text>
        <ActivityIndicator color="#E9F2EC" />
      </View>
    );
  }

  if (mode.screen === 'email') {
    return (
      <EmailSignInScreen
        onOpenReviewAccess={() => setMode({ screen: 'review' })}
        onSignedIn={(me) => void applyMe(me)}
      />
    );
  }

  if (mode.screen === 'review') {
    return (
      <ReviewAccessScreen
        onCancel={() => setMode({ screen: 'email' })}
        onSignedIn={(me) => void applyMe(me)}
      />
    );
  }

  if (mode.screen === 'callback') {
    return (
      <AuthCallbackScreen
        challengeId={mode.challengeId}
        emailCode={mode.emailCode}
        onRestart={() => setMode({ screen: 'email' })}
        onSignedIn={(me) => void applyMe(me)}
      />
    );
  }

  if (mode.screen === 'onboarding') {
    return <>{renderOnboarding(mode.me)}</>;
  }

  return <>{children(mode.me)}</>;
}

const styles = StyleSheet.create({
  shell: {
    flex: 1,
    backgroundColor: '#103C27',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  brand: {
    color: '#E9F2EC',
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 16,
  },
});
