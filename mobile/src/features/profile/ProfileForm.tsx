import { useState } from 'react';
import { Switch, TextInput, View } from 'react-native';

import { ApiRequestError } from '@/api/errors';
import { space } from '@/design/tokens';
import {
  updateProfile,
  type ProfileUpdateBody,
} from '@/features/profile/api';
import { Button, ScrollScreen, Text } from '@/ui/primitives';

type Props = {
  historyEpoch: number;
  onComplete: () => void;
  onEpochConflict: () => void;
};

const defaultBody = (historyEpoch: number): ProfileUpdateBody => ({
  display_name: '',
  preferred_club: 'iron',
  primary_goal: 'consistency',
  experience_mode: 'improve',
  handedness: 'right',
  camera_angle: 'face-on',
  practice_minutes: 20,
  sessions_per_week: 2,
  handicap_range: null,
  marketing_email_opt_in: false,
  reduced_motion: false,
  expected_history_epoch: historyEpoch,
});

export function ProfileForm({ historyEpoch, onComplete, onEpochConflict }: Props) {
  const [form, setForm] = useState<ProfileUpdateBody>(defaultBody(historyEpoch));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (!form.display_name.trim()) {
        setError('Display name is required.');
        return;
      }
      await updateProfile({
        ...form,
        display_name: form.display_name.trim(),
        expected_history_epoch: historyEpoch,
        marketing_email_opt_in: form.marketing_email_opt_in ?? false,
      });
      onComplete();
    } catch (err) {
      if (err instanceof ApiRequestError && err.appError.status === 409) {
        onEpochConflict();
        return;
      }
      setError('Could not save your profile. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollScreen accessibilityLabel="Profile onboarding">
      <Text size="brand" weight="700">
        CaddieInsight
      </Text>
      <Text size="title" weight="600" style={{ marginTop: space.sm }}>
        Set up your coaching profile
      </Text>
      <Text tone="muted" style={{ marginVertical: space.md }}>
        Tell your caddie how you play so Today stays personal.
      </Text>
      <Text weight="600">Display name</Text>
      <TextInput
        value={form.display_name}
        onChangeText={(display_name) => setForm((f) => ({ ...f, display_name }))}
        accessibilityLabel="Display name"
        style={{
          minHeight: 48,
          borderWidth: 1,
          borderColor: '#7A867C',
          borderRadius: 14,
          paddingHorizontal: 14,
          marginTop: 8,
          marginBottom: 16,
        }}
      />
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 16,
          minHeight: 48,
        }}
      >
        <Text>Email me occasional tips</Text>
        <Switch
          value={form.marketing_email_opt_in}
          onValueChange={(marketing_email_opt_in) =>
            setForm((f) => ({ ...f, marketing_email_opt_in }))
          }
          accessibilityLabel="Marketing email opt in"
        />
      </View>
      {error ? <Text tone="danger">{error}</Text> : null}
      <Button
        label={busy ? 'Saving…' : 'Save and continue'}
        onPress={() => void submit()}
        disabled={busy}
      />
    </ScrollScreen>
  );
}
