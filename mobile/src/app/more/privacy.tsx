import { useState } from 'react';
import { Alert, TextInput } from 'react-native';
import { router } from 'expo-router';

import { PrivateCache } from '@/platform/privateCache';
import {
  createPrivacyExport,
  exchangePrivacyStepUp,
  readSessionKind,
  requestAccountDeletion,
  requestHistoryReset,
  startPrivacyStepUp,
} from '@/features/more/privacy';
import { createIdempotencyKey } from '@/features/auth/api';
import { Button, ScrollScreen, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';

export default function PrivacyRoute() {
  const [purpose, setPurpose] = useState<
    'data_export' | 'history_reset' | 'account_delete' | null
  >(null);
  const [code, setCode] = useState('');
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [verifier, setVerifier] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function begin(next: 'data_export' | 'history_reset' | 'account_delete') {
    const session = await readSessionKind();
    if (session?.kind === 'store_review') {
      setMessage(
        'Store-review privacy uses the review step-up path. Enter the review credential when prompted in a later build slice; email codes are not used.',
      );
      setPurpose(next);
      return;
    }
    try {
      const started = await startPrivacyStepUp(next);
      setPurpose(next);
      setChallengeId(started.challengeId);
      setVerifier(started.verifier);
      setMessage('Enter the email code to continue.');
    } catch {
      setMessage('Could not start privacy verification.');
    }
  }

  async function confirm() {
    if (!purpose || !challengeId || !verifier) {
      return;
    }
    try {
      const key = await createIdempotencyKey();
      const token = await exchangePrivacyStepUp({
        challengeId,
        code,
        verifier,
        idempotencyKey: key,
      });
      setCode('');
      const accountId = PrivateCache.getActiveAccountId() ?? 'local';
      if (purpose === 'data_export') {
        const receipt = await createPrivacyExport(token);
        setMessage(`Export ${receipt.status}. ID ${receipt.export_id}`);
      } else if (purpose === 'history_reset') {
        const result = await requestHistoryReset({
          stepUpToken: token,
          expectedHistoryEpoch: 0,
          accountId,
        });
        setMessage(result === 'done' ? 'History reset complete.' : 'History reset pending…');
      } else {
        Alert.alert(
          'Delete account?',
          'Deleting CaddieInsight does not cancel Apple or Google Play auto-renewal. Manage billing in the store subscription settings.',
          [
            { text: 'Cancel', style: 'cancel' },
            {
              text: 'Delete',
              style: 'destructive',
              onPress: () => {
                void requestAccountDeletion({ stepUpToken: token, accountId }).then(
                  (result) => {
                    setMessage(
                      result === 'done'
                        ? 'Account deleted.'
                        : 'Account deletion pending…',
                    );
                    if (result === 'done') {
                      router.replace('/(auth)');
                    }
                  },
                );
              },
            },
          ],
        );
      }
    } catch {
      setCode('');
      setMessage('Verification failed. Try again.');
    }
  }

  return (
    <ScrollScreen accessibilityLabel="Privacy">
      <Text size="brand" weight="700">
        Privacy
      </Text>
      <Text tone="muted" style={{ marginVertical: space.md }}>
        Export, history reset, and account deletion require a fresh verification.
      </Text>
      <Button label="Export my data" variant="secondary" onPress={() => void begin('data_export')} />
      <Button
        label="Reset swing history"
        variant="secondary"
        onPress={() => void begin('history_reset')}
      />
      <Button
        label="Delete account"
        variant="danger"
        onPress={() => void begin('account_delete')}
      />
      {purpose && challengeId ? (
        <>
          <TextInput
            value={code}
            onChangeText={setCode}
            placeholder="Email code"
            keyboardType="number-pad"
            accessibilityLabel="Privacy verification code"
            style={{
              minHeight: 48,
              borderWidth: 1,
              borderColor: '#7A867C',
              borderRadius: 14,
              paddingHorizontal: 14,
              marginVertical: 12,
            }}
          />
          <Button label="Confirm" onPress={() => void confirm()} />
        </>
      ) : null}
      {message ? <Text style={{ marginTop: space.md }}>{message}</Text> : null}
    </ScrollScreen>
  );
}
