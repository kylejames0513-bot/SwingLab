import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { View } from 'react-native';

import { ApiRequestError } from '@/api/errors';
import { fetchProgress, type ProgressResponse } from '@/features/progress/api';
import { space } from '@/design/tokens';
import {
  AsyncState,
  Button,
  Card,
  ScrollScreen,
  StatusBadge,
  Text,
} from '@/ui/primitives';

export function ProgressScreen() {
  const [status, setStatus] = useState<'loading' | 'error' | 'ready' | 'empty'>(
    'loading',
  );
  const [error, setError] = useState<string | undefined>();
  const [progress, setProgress] = useState<ProgressResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchProgress();
        if (cancelled) {
          return;
        }
        setProgress(data);
        setStatus(data.groups.length === 0 ? 'empty' : 'ready');
      } catch (err) {
        if (cancelled) {
          return;
        }
        setStatus('error');
        setError(
          err instanceof ApiRequestError
            ? 'Could not load Progress.'
            : 'Could not load Progress.',
        );
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ScrollScreen accessibilityLabel="Progress">
      <Text size="brand" weight="700">
        Progress
      </Text>
      <Text tone="muted" style={{ marginBottom: space.md }}>
        Transfer outcomes from your Proof Cycles.
      </Text>
      <AsyncState status={status} errorMessage={error}>
        {progress?.groups.map((group) => (
          <Card key={`${group.club}-${group.hand}-${group.angle}`} style={{ marginBottom: space.md }}>
            <Text weight="700">
              {group.club} · {group.hand} · {group.angle}
            </Text>
            <View style={{ marginTop: space.sm, gap: 8 }}>
              <StatusBadge label={group.outcome_label} tone="neutral" />
              <Text>{group.summary}</Text>
              <Text tone="muted">Decision: {group.decision_label}</Text>
              <Text tone="muted">{group.next_step}</Text>
              {group.proof_cycle_target ? (
                <Button
                  label="Matched re-film"
                  onPress={() => {
                    const target = group.proof_cycle_target!;
                    router.push({
                      pathname: '/capture',
                      params: {
                        mode: 'matched',
                        baseline_session_id: target.baseline_session_id,
                        target_fingerprint: target.target_fingerprint,
                        drill_id: target.drill_id,
                        club: target.club,
                        hand: target.hand,
                        angle: target.angle,
                      },
                    });
                  }}
                />
              ) : null}
            </View>
          </Card>
        ))}
      </AsyncState>
    </ScrollScreen>
  );
}
