import { useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { AuthStore } from '@/auth/authStore';
import {
  clearPendingAuth,
  createIdempotencyKey,
  defaultDeviceLabel,
  exchangeReviewSignIn,
  fetchMe,
  getOrCreateInstallationId,
  readPendingAuth,
  savePendingAuth,
  startReviewSignIn,
} from '@/features/auth/api';
import { createPKCE } from '@/features/auth/pkce';

type Props = {
  onSignedIn: (me: Awaited<ReturnType<typeof fetchMe>>) => void;
  onCancel: () => void;
};

function reviewProvider(): 'apple' | 'google' {
  return Platform.OS === 'ios' ? 'apple' : 'google';
}

export function ReviewAccessScreen({ onSignedIn, onCancel }: Props) {
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [needsPasswordRetry, setNeedsPasswordRetry] = useState(false);
  const provider = reviewProvider();

  async function submit() {
    setBusy(true);
    setMessage(null);
    try {
      const { verifier, challenge } = await createPKCE();
      const installationId = await getOrCreateInstallationId();
      const idempotencyKey = await createIdempotencyKey();
      const started = await startReviewSignIn({
        account: account.trim(),
        deviceLabel: defaultDeviceLabel(),
        installationId,
        challenge,
        provider,
      });
      await savePendingAuth({
        challengeId: started.challenge_id,
        verifier,
        idempotencyKey,
        kind: 'store_review',
        provider,
        startedAt: Date.now(),
      });

      const { result } = await exchangeReviewSignIn({
        challengeId: started.challenge_id,
        password,
        verifier,
        idempotencyKey,
      });

      // Clear controlled credential from local component state after attempt.
      setPassword('');

      if (result.status === 'pending') {
        setNeedsPasswordRetry(true);
        setMessage('Securing this device… Re-enter the review credential to retry.');
        return;
      }
      if (result.status !== 'authenticated' || !result.access_token) {
        setMessage('Review access is unavailable.');
        onCancel();
        return;
      }
      await AuthStore.completeExchange(result.access_token, {
        kind: 'store_review',
        provider,
      });
      await clearPendingAuth();
      const me = await fetchMe();
      onSignedIn(me);
    } catch {
      setPassword('');
      setMessage('Review access is unavailable.');
      onCancel();
    } finally {
      setBusy(false);
    }
  }

  async function retryPending() {
    setBusy(true);
    setMessage(null);
    try {
      const pending = await readPendingAuth();
      if (!pending || pending.kind !== 'store_review') {
        onCancel();
        return;
      }
      const { result } = await exchangeReviewSignIn({
        challengeId: pending.challengeId,
        password,
        verifier: pending.verifier,
        idempotencyKey: pending.idempotencyKey,
      });
      setPassword('');
      if (result.status === 'pending') {
        setMessage('Still securing this device…');
        return;
      }
      if (result.status !== 'authenticated' || !result.access_token) {
        setMessage('Review access is unavailable.');
        onCancel();
        return;
      }
      await AuthStore.completeExchange(result.access_token, {
        kind: 'store_review',
        provider: pending.provider ?? provider,
      });
      await clearPendingAuth();
      const me = await fetchMe();
      onSignedIn(me);
    } catch {
      setPassword('');
      setMessage('Review access is unavailable.');
      onCancel();
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.root} accessibilityLabel="App review access">
      <Text style={styles.brand} accessibilityRole="header">
        CaddieInsight
      </Text>
      <Text style={styles.title}>App review access</Text>
      <Text style={styles.copy}>
        For {provider === 'apple' ? 'App Store' : 'Play Store'} review only. Uses
        this build’s {provider} lane.
      </Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="Review account"
        value={account}
        onChangeText={setAccount}
        editable={!busy}
        accessibilityLabel="Review account"
      />
      <TextInput
        style={styles.input}
        secureTextEntry
        placeholder="Review password"
        value={password}
        onChangeText={setPassword}
        editable={!busy}
        accessibilityLabel="Review password"
      />
      {message ? <Text style={styles.message}>{message}</Text> : null}
      {busy ? <ActivityIndicator color="#E9F2EC" /> : null}
      <Pressable
        style={styles.button}
        onPress={() => void (needsPasswordRetry ? retryPending() : submit())}
        disabled={busy}
        accessibilityRole="button"
        accessibilityLabel="Continue review access"
      >
        <Text style={styles.buttonText}>Continue</Text>
      </Pressable>
      <Pressable
        style={styles.linkButton}
        onPress={onCancel}
        accessibilityRole="button"
        accessibilityLabel="Back to email sign-in"
      >
        <Text style={styles.linkText}>Back to email sign-in</Text>
      </Pressable>
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
  title: {
    color: '#F7F5F0',
    fontSize: 22,
    fontWeight: '600',
    marginBottom: 8,
  },
  copy: {
    color: '#B7C9BF',
    fontSize: 16,
    lineHeight: 22,
    marginBottom: 20,
  },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: '#7A867C',
    borderRadius: 14,
    paddingHorizontal: 14,
    color: '#F7F5F0',
    marginBottom: 12,
  },
  button: {
    minHeight: 48,
    borderRadius: 14,
    backgroundColor: '#1A5C38',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
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
    marginTop: 12,
  },
  linkText: {
    color: '#FFAD62',
    fontSize: 16,
  },
  message: {
    color: '#F7F5F0',
    fontSize: 15,
    marginBottom: 12,
  },
});
