import { useLocalSearchParams } from 'expo-router';

import { useAnalysisJob } from '@/features/analysis/useAnalysisJob';
import { ScrollScreen, StatusBadge, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';

export default function AnalysisSessionRoute() {
  const params = useLocalSearchParams<{ sessionId?: string }>();
  const sessionId = typeof params.sessionId === 'string' ? params.sessionId : null;
  const { session, error } = useAnalysisJob(sessionId);

  return (
    <ScrollScreen accessibilityLabel="Analysis status">
      <Text size="brand" weight="700">
        CaddieInsight
      </Text>
      <Text tone="muted" style={{ marginBottom: space.md }}>
        Analysis
      </Text>
      {error ? <Text tone="danger">{error}</Text> : null}
      {session ? (
        <>
          <StatusBadge
            label={session.status}
            tone={
              session.status === 'done'
                ? 'success'
                : session.status === 'failed'
                  ? 'danger'
                  : 'warn'
            }
          />
          <Text style={{ marginTop: space.md }}>
            Session {session.id} · {session.club} · {session.angle}
          </Text>
        </>
      ) : (
        <Text tone="muted">Waiting for server status…</Text>
      )}
    </ScrollScreen>
  );
}
