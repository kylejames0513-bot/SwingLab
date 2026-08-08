import { useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';

import { createIdempotencyKey } from '@/features/auth/api';
import {
  flushQueuedPracticeEvidence,
  queuePracticeEvidence,
  submitPracticeEvidence,
  type PracticeEvidenceRequest,
} from '@/features/practice/api';
import { space } from '@/design/tokens';
import { Button, ScrollScreen, Text } from '@/ui/primitives';

type Props = {
  baselineSessionId?: string;
  targetFingerprint?: string;
  drillId?: string;
  historyEpoch?: number;
};

const DURATIONS = [10, 20, 45] as const;

export function PracticeScreen({
  baselineSessionId = '',
  targetFingerprint = '',
  drillId = '',
  historyEpoch = 0,
}: Props) {
  const [minutes, setMinutes] = useState<(typeof DURATIONS)[number]>(20);
  const [remaining, setRemaining] = useState(0);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void flushQueuedPracticeEvidence();
  }, []);

  useEffect(() => {
    if (!running || remaining <= 0) {
      return;
    }
    const timer = setTimeout(() => setRemaining((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [running, remaining]);

  async function complete(outcome: PracticeEvidenceRequest['outcome']) {
    if (!baselineSessionId || !targetFingerprint || !drillId) {
      setMessage('Open a Brief first so practice can attach to your Proof Cycle.');
      return;
    }
    const body: PracticeEvidenceRequest = {
      baseline_session_id: baselineSessionId,
      target_fingerprint: targetFingerprint,
      drill_id: drillId,
      minutes,
      outcome,
      reps: Math.max(1, Math.round(minutes * 2)),
      feel: 'same',
      relative_strike: 'unknown',
      start_line: 'unknown',
      miss_pattern: 'unknown',
      expected_history_epoch: historyEpoch,
    };
    const key = await createIdempotencyKey();
    try {
      await submitPracticeEvidence(body, key);
      setMessage('Practice logged.');
      setRunning(false);
    } catch {
      await queuePracticeEvidence(body, key);
      setMessage('Saved offline. We’ll sync when you’re back online.');
      setRunning(false);
    }
  }

  return (
    <ScrollScreen accessibilityLabel="Practice">
      <Text size="brand" weight="700">
        Practice
      </Text>
      <Text tone="muted" style={{ marginVertical: space.md }}>
        Same server experiment at 10, 20, or 45 minutes.
      </Text>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: space.md }}>
        {DURATIONS.map((value) => (
          <Pressable
            key={value}
            onPress={() => setMinutes(value)}
            accessibilityRole="button"
            accessibilityLabel={`${value} minutes`}
            style={{
              minHeight: 48,
              minWidth: 72,
              borderRadius: 14,
              borderWidth: 1,
              borderColor: minutes === value ? '#1A5C38' : '#7A867C',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: minutes === value ? '#EDF3EE' : '#FFFDF9',
            }}
          >
            <Text weight="600">{value}m</Text>
          </Pressable>
        ))}
      </View>
      <Text size="title" weight="700" style={{ marginBottom: space.md }}>
        {running
          ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`
          : `${minutes}:00`}
      </Text>
      <Button
        label={running ? 'Pause' : 'Start timer'}
        onPress={() => {
          if (!running && remaining === 0) {
            setRemaining(minutes * 60);
          }
          setRunning((value) => !value);
        }}
      />
      <Button
        label="Log completed practice"
        variant="secondary"
        onPress={() => void complete('completed')}
      />
      <Button
        label="Still working"
        variant="secondary"
        onPress={() => void complete('still_working')}
      />
      {message ? (
        <Text style={{ marginTop: space.md }} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}
    </ScrollScreen>
  );
}
