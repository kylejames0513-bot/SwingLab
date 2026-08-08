import { useState } from 'react';
import {
  ActivityIndicator,
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
  exchangeEmailSignIn,
  fetchMe,
  getOrCreateInstallationId,
  readPendingAuth,
  savePendingAuth,
  startEmailSignIn,
} from '@/features/auth/api';
import { createPKCE, normalizeEmail, normalizeEmailCode } from '@/features/auth/pkce';

type Props = {
  onSignedIn: (me: Awaited<ReturnType<typeof fetchMe>>) => void;
  onOpenReviewAccess: () => void;
};

export function EmailSignInScreen({ onSignedIn, onOpenReviewAccess }: Props) {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [phase, setPhase] = useState<'email' | 'code' | 'securing'>('email');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resendAt, setResendAt] = useState(0);
  const [nowTick, setNowTick] = useState(0);
  const canResend = nowTick >= resendAt;

  async function sendLink() {
    setBusy(true);
    setMessage(null);
    try {
      const normalized = normalizeEmail(email);
      if (!normalized.includes('@')) {
        setMessage('Enter a valid email address.');
        return;
      }
      const { verifier, challenge } = await createPKCE();
      const installationId = await getOrCreateInstallationId();
      const idempotencyKey = await createIdempotencyKey();
      const started = await startEmailSignIn({
        email: normalized,
        deviceLabel: defaultDeviceLabel(),
        installationId,
        challenge,
      });
      await savePendingAuth({
        challengeId: started.challenge_id,
        verifier,
        idempotencyKey,
        kind: 'ordinary',
        startedAt: Date.now(),
      });
      setPhase('code');
      setResendAt(Date.now() + 30_000);
      setNowTick(Date.now());
      setMessage(
        'If an account exists for that email, we sent a sign-in link and code.',
      );
    } catch {
      setMessage('Unable to start sign-in right now. Try again shortly.');
    } finally {
      setBusy(false);
    }
  }

  async function submitCode() {
    setBusy(true);
    setMessage(null);
    setPhase('securing');
    try {
      const pending = await readPendingAuth();
      if (!pending || pending.kind !== 'ordinary') {
        setPhase('email');
        setMessage('Start sign-in on this device first.');
        return;
      }
      const digits = normalizeEmailCode(code);
      if (digits.length !== 8) {
        setPhase('code');
        setMessage('Enter the 8-digit code from your email.');
        return;
      }
      const { result } = await exchangeEmailSignIn({
        challengeId: pending.challengeId,
        emailCode: digits,
        verifier: pending.verifier,
        idempotencyKey: pending.idempotencyKey,
      });
      if (result.status === 'pending') {
        setMessage('Securing this device…');
        setPhase('securing');
        return;
      }
      if (result.status !== 'authenticated' || !result.access_token) {
        setPhase('code');
        setMessage('That code could not be verified. Try again.');
        return;
      }
      await AuthStore.completeExchange(result.access_token, { kind: 'ordinary' });
      await clearPendingAuth();
      const me = await fetchMe();
      onSignedIn(me);
    } catch {
      setPhase('code');
      setMessage('That code could not be verified. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.root} accessibilityLabel="Email sign-in">
      <Text style={styles.brand} accessibilityRole="header">
        CaddieInsight
      </Text>
      <Text style={styles.title}>Sign in with email</Text>
      <Text style={styles.copy}>
        We’ll email a one-time link and code. No password for everyday access.
      </Text>

      {phase === 'email' || phase === 'code' ? (
        <TextInput
          style={styles.input}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
          placeholder="you@example.com"
          value={email}
          editable={phase === 'email' && !busy}
          onChangeText={setEmail}
          accessibilityLabel="Email"
        />
      ) : null}

      {phase === 'code' || phase === 'securing' ? (
        <TextInput
          style={styles.input}
          keyboardType="number-pad"
          textContentType="oneTimeCode"
          placeholder="1234-5678"
          value={code}
          editable={phase === 'code' && !busy}
          onChangeText={setCode}
          accessibilityLabel="Email code"
        />
      ) : null}

      {message ? (
        <Text style={styles.message} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}

      {busy ? <ActivityIndicator color="#E9F2EC" /> : null}

      {phase === 'email' ? (
        <Pressable
          style={styles.button}
          onPress={() => void sendLink()}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel="Send sign-in email"
        >
          <Text style={styles.buttonText}>Send sign-in email</Text>
        </Pressable>
      ) : null}

      {phase === 'code' ? (
        <>
          <Pressable
            style={styles.button}
            onPress={() => void submitCode()}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel="Verify code"
          >
            <Text style={styles.buttonText}>Verify code</Text>
          </Pressable>
          <Pressable
            style={styles.linkButton}
            onPress={() => {
              setNowTick(Date.now());
              if (Date.now() >= resendAt) {
                void sendLink();
              }
            }}
            disabled={busy || !canResend}
            accessibilityRole="button"
            accessibilityLabel="Resend email"
          >
            <Text style={styles.linkText}>
              {canResend ? 'Resend email' : 'Resend available shortly'}
            </Text>
          </Pressable>
        </>
      ) : null}

      {phase === 'securing' ? (
        <Text style={styles.message}>Securing this device…</Text>
      ) : null}

      <Pressable
        style={styles.linkButton}
        onPress={onOpenReviewAccess}
        accessibilityRole="button"
        accessibilityLabel="App review access"
      >
        <Text style={styles.linkText}>App review access</Text>
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
