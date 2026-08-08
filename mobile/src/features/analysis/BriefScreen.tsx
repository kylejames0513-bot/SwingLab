import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { View } from 'react-native';

import { ApiRequestError } from '@/api/errors';
import {
  fetchBrief,
  type BriefResponse,
} from '@/features/analysis/briefApi';
import { space } from '@/design/tokens';
import {
  AsyncState,
  Button,
  Card,
  ScrollScreen,
  StatusBadge,
  Text,
} from '@/ui/primitives';

type Props = {
  sessionId: string;
};

export function BriefScreen({ sessionId }: Props) {
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading');
  const [error, setError] = useState<string | undefined>();
  const [brief, setBrief] = useState<BriefResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchBrief(sessionId);
        if (!cancelled) {
          setBrief(data);
          setStatus('ready');
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        setStatus('error');
        if (err instanceof ApiRequestError && err.appError.status === 404) {
          setError('This Brief is not available on this account.');
        } else {
          setError('Could not load the Caddie Brief.');
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return (
    <ScrollScreen accessibilityLabel="Caddie Brief">
      <Text size="brand" weight="700">
        CaddieInsight
      </Text>
      <Text size="title" weight="600" style={{ marginTop: space.sm }}>
        Caddie Brief
      </Text>
      <AsyncState status={status} errorMessage={error}>
        {brief ? (
          <View style={{ gap: space.md, marginTop: space.md }}>
            <StatusBadge
              label={brief.status.replace(/_/g, ' ')}
              tone={
                brief.status === 'coaching_ready'
                  ? 'success'
                  : brief.status === 'refilm_required'
                    ? 'danger'
                    : 'warn'
              }
            />
            {brief.priority ? (
              <Card>
                <Text weight="700">Priority</Text>
                <Text style={{ marginTop: space.sm }}>{brief.priority.name}</Text>
                {brief.priority.value != null ? (
                  <Text tone="muted">Value: {String(brief.priority.value)}</Text>
                ) : null}
              </Card>
            ) : null}
            {brief.evidence ? (
              <Card>
                <Text weight="700">Evidence</Text>
                <Text style={{ marginTop: space.sm }}>
                  Recurring sessions: {brief.evidence.recurring_sessions}
                </Text>
                <Text>Remaining issues: {brief.evidence.remaining_issues}</Text>
                {brief.evidence.strength ? (
                  <Text tone="muted">Strength: {brief.evidence.strength}</Text>
                ) : null}
              </Card>
            ) : null}
            <Card>
              <Text weight="700">Confidence</Text>
              <Text style={{ marginTop: space.sm }}>{brief.confidence}</Text>
              {brief.hypothesis ? (
                <Text tone="muted" style={{ marginTop: space.sm }}>
                  {brief.hypothesis}
                </Text>
              ) : null}
            </Card>
            {brief.prescribed_drill ? (
              <Card>
                <Text weight="700">Prescribed drill</Text>
                <Text style={{ marginTop: space.sm }}>
                  {brief.prescribed_drill.name}
                </Text>
                <Text tone="muted">{brief.prescribed_drill.aim}</Text>
                <Text tone="muted">Dosage: {brief.prescribed_drill.dosage}</Text>
                <Text tone="muted">
                  Pass mark: {brief.prescribed_drill.pass_mark}
                </Text>
              </Card>
            ) : null}
            {brief.proof_cycle_target ? (
              <Button
                label="Matched re-film"
                onPress={() => {
                  const target = brief.proof_cycle_target!;
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
            <Button
              label="Practice this drill"
              variant="secondary"
              onPress={() => router.push('/(tabs)/practice')}
            />
          </View>
        ) : null}
      </AsyncState>
    </ScrollScreen>
  );
}
