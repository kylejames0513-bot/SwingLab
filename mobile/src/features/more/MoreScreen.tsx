import { useEffect, useState } from 'react';
import { Alert, Linking, Switch, View } from 'react-native';
import { router } from 'expo-router';

import { apiRequest } from '@/api/client';
import { confirmSignOut } from '@/features/auth/signOut';
import { openGearStore, isAllowedGearHost } from '@/features/more/gear';
import {
  getPracticeRemindersEnabled,
  registerForPushAfterOptIn,
  setPracticeReminders,
} from '@/platform/notifications';
import { space } from '@/design/tokens';
import { Button, ScrollScreen, Text } from '@/ui/primitives';
import Constants from 'expo-constants';

type Capabilities = {
  capabilities?: {
    physical_store_url?: string | null;
    features?: { push?: boolean; privacy?: boolean };
  };
};

export function MoreScreen() {
  const [gearUrl, setGearUrl] = useState<string | null>(null);
  const [reminders, setReminders] = useState(getPracticeRemindersEnabled());
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const caps = await apiRequest<Capabilities>('/api/v1/capabilities');
        if (!cancelled) {
          setGearUrl(caps.capabilities?.physical_store_url ?? null);
        }
      } catch {
        // Non-blocking.
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ScrollScreen accessibilityLabel="More">
      <Text size="brand" weight="700">
        More
      </Text>
      <Text tone="muted" style={{ marginBottom: space.md }}>
        Account, privacy, devices, and gear.
      </Text>

      <Button label="Profile" variant="secondary" onPress={() => router.push('/more/profile')} />
      <Button label="Pro" variant="secondary" onPress={() => router.push('/more/pro')} />
      <Button label="Devices" variant="secondary" onPress={() => router.push('/more/devices')} />
      <Button label="Privacy" variant="secondary" onPress={() => router.push('/more/privacy')} />

      <Button
        label="Browse gear"
        variant="secondary"
        onPress={() => {
          if (!gearUrl || !isAllowedGearHost(gearUrl, gearUrl)) {
            setMessage('Gear store is not configured for this environment.');
            return;
          }
          void openGearStore(gearUrl).catch(() =>
            setMessage('Could not open the gear store.'),
          );
        }}
      />

      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          minHeight: 48,
          marginVertical: space.md,
        }}
      >
        <Text>Practice reminders</Text>
        <Switch
          value={reminders}
          accessibilityLabel="Practice reminders"
          onValueChange={(value) => {
            setReminders(value);
            void (async () => {
              if (value) {
                await registerForPushAfterOptIn({
                  appVersion: Constants.expoConfig?.version ?? '1.0.0',
                  practiceRemindersEnabled: true,
                });
              }
              await setPracticeReminders(value).catch(() => undefined);
            })();
          }}
        />
      </View>

      <Button
        label="Enable analysis notifications"
        variant="secondary"
        onPress={() => {
          void registerForPushAfterOptIn({
            appVersion: Constants.expoConfig?.version ?? '1.0.0',
          }).then((result) => {
            setMessage(
              result === 'registered'
                ? 'Notifications enabled.'
                : result === 'denied'
                  ? 'Notification permission denied.'
                  : 'Push registration skipped (missing EAS project id).',
            );
          });
        }}
      />

      <Button
        label="Support"
        variant="secondary"
        onPress={() => void Linking.openURL('https://caddieinsight.com/help')}
      />
      <Button
        label="Terms"
        variant="secondary"
        onPress={() => void Linking.openURL('https://caddieinsight.com/terms')}
      />
      <Button
        label="Privacy Policy"
        variant="secondary"
        onPress={() =>
          void Linking.openURL('https://caddieinsight.com/privacy')
        }
      />

      <Button
        label="Sign out"
        variant="danger"
        onPress={() => {
          Alert.alert('Sign out?', 'You can sign back in on this device.', [
            { text: 'Cancel', style: 'cancel' },
            {
              text: 'Sign out',
              style: 'destructive',
              onPress: () => {
                void confirmSignOut({
                  hasStagedUpload: false,
                  discardLocalWork: true,
                }).then(() => router.replace('/(auth)'));
              },
            },
          ]);
        }}
      />

      {message ? (
        <Text style={{ marginTop: space.md }} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}
    </ScrollScreen>
  );
}
