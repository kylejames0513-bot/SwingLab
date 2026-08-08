import { router } from 'expo-router';

import { confirmSignOut, signOutPrompt } from '@/features/auth/signOut';
import { Button, ScrollScreen, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';
import { Alert, Linking } from 'react-native';
import { getAppEnvironment } from '@/config/env';

export default function MoreRoute() {
  return (
    <ScrollScreen accessibilityLabel="More">
      <Text size="brand" weight="700">
        More
      </Text>
      <Text tone="muted" style={{ marginBottom: space.md }}>
        Account, privacy, and gear.
      </Text>
      <Button
        label="Browse gear"
        variant="secondary"
        onPress={() => {
          const env = getAppEnvironment();
          void Linking.openURL(`${env.apiOrigin}/`);
        }}
      />
      <Button
        label="Sign out"
        variant="danger"
        onPress={() => {
          const prompt = signOutPrompt(false);
          if (prompt.kind === 'staged_upload') {
            Alert.alert('Unfinished upload', 'Keep working or discard and sign out?', [
              { text: 'Keep working', style: 'cancel' },
              {
                text: 'Discard and sign out',
                style: 'destructive',
                onPress: () => {
                  void confirmSignOut({
                    hasStagedUpload: true,
                    discardLocalWork: true,
                  }).then(() => router.replace('/(auth)'));
                },
              },
            ]);
            return;
          }
          void confirmSignOut({
            hasStagedUpload: false,
            discardLocalWork: true,
          }).then(() => router.replace('/(auth)'));
        }}
      />
    </ScrollScreen>
  );
}
