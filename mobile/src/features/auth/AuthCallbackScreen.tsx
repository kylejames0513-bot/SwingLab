import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { AuthStore } from '@/auth/authStore';
import {
  clearPendingAuth,
  exchangeEmailSignIn,
  fetchMe,
  readPendingAuth,
} from '@/features/auth/api';
import { normalizeEmailCode } from '@/features/auth/pkce';

type Props = {
  challengeId?: string | null;
  emailCode?: string | null;
  onSignedIn: (me: Awaited<ReturnType<typeof fetchMe>>) => void;
  onRestart: () => void;
};

type CallbackState =
  | { status: 'working' }
  | { status: 'securing' }
  | { status: 'other_device' }
  | { status: 'invalid' }
  | { status: 'error'; message: string };

export function AuthCallbackScreen({
  challengeId,
  emailCode,
  onSignedIn,
  onRestart,
}: Props) {
  const [state, setState] = useState<CallbackState>({ status: 'working' });

  useEffect(() => {
    let cancelled = false;

    async function run() {
      if (!challengeId || !emailCode) {
        setState({ status: 'invalid' });
        return;
      }
      const pending = await readPendingAuth();
      if (!pending || pending.challengeId !== challengeId) {
        // Second device / missing local verifier — never call exchange.
        setState({ status: 'other_device' });
        return;
      }
      const digits = normalizeEmailCode(emailCode);
      if (digits.length !== 8) {
        setState({ status: 'invalid' });
        return;
      }

      try {
        setState({ status: 'securing' });
        const { result } = await exchangeEmailSignIn({
          challengeId: pending.challengeId,
          emailCode: digits,
          verifier: pending.verifier,
          idempotencyKey: pending.idempotencyKey,
        });
        if (cancelled) {
          return;
        }
        if (result.status === 'pending') {
          setState({ status: 'securing' });
          return;
        }
        if (result.status !== 'authenticated' || !result.access_token) {
          setState({ status: 'invalid' });
          return;
        }
        if (pending.kind === 'store_review') {
          await AuthStore.completeExchange(result.access_token, {
            kind: 'store_review',
            provider: pending.provider ?? 'apple',
          });
        } else {
          await AuthStore.completeExchange(result.access_token, {
            kind: 'ordinary',
          });
        }
        await clearPendingAuth();
        const me = await fetchMe();
        if (!cancelled) {
          onSignedIn(me);
        }
      } catch {
        if (!cancelled) {
          setState({
            status: 'error',
            message: 'This sign-in link could not be completed.',
          });
        }
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [challengeId, emailCode, onSignedIn]);

  return (
    <View style={styles.root} accessibilityLabel="Sign-in callback">
      <Text style={styles.brand}>CaddieInsight</Text>
      {state.status === 'working' || state.status === 'securing' ? (
        <>
          <Text style={styles.copy}>Securing this device…</Text>
          <ActivityIndicator color="#E9F2EC" />
          <Pressable
            style={styles.linkButton}
            onPress={onRestart}
            accessibilityRole="button"
            accessibilityLabel="Cancel and restart sign-in"
          >
            <Text style={styles.linkText}>Cancel and restart</Text>
          </Pressable>
        </>
      ) : null}
      {state.status === 'other_device' ? (
        <>
          <Text style={styles.copy}>
            This sign-in was started on another device. Finish it there, or start
            fresh on this phone.
          </Text>
          <Pressable
            style={styles.button}
            onPress={onRestart}
            accessibilityRole="button"
            accessibilityLabel="Start fresh sign-in"
          >
            <Text style={styles.buttonText}>Start fresh sign-in</Text>
          </Pressable>
        </>
      ) : null}
      {state.status === 'invalid' || state.status === 'error' ? (
        <>
          <Text style={styles.copy}>
            {state.status === 'error'
              ? state.message
              : 'This sign-in link is invalid or expired.'}
          </Text>
          <Pressable
            style={styles.button}
            onPress={onRestart}
            accessibilityRole="button"
            accessibilityLabel="Restart sign-in"
          >
            <Text style={styles.buttonText}>Restart sign-in</Text>
          </Pressable>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#103C27',
    paddingHorizontal: 24,
    paddingTop: 72,
  },
  brand: {
    color: '#E9F2EC',
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 24,
  },
  copy: {
    color: '#B7C9BF',
    fontSize: 16,
    lineHeight: 22,
    marginBottom: 20,
  },
  button: {
    minHeight: 48,
    borderRadius: 14,
    backgroundColor: '#1A5C38',
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonText: {
    color: '#E9F2EC',
    fontSize: 17,
    fontWeight: '600',
  },
  linkButton: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 16,
  },
  linkText: {
    color: '#FFAD62',
    fontSize: 16,
  },
});
