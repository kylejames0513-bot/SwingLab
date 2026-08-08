import { useEffect, useState } from 'react';

import { ApiRequestError } from '@/api/errors';
import {
  AsyncState,
  Button,
  Card,
  Screen,
  ScrollScreen,
  StatusBadge,
  Text,
} from '@/ui/primitives';
import { fetchToday, type MobileTodayResponse } from '@/features/today/api';
import { space } from '@/design/tokens';
import { View } from 'react-native';

type Props = {
  onAnalyze: () => void;
};

function nextAction(today: MobileTodayResponse): {
  title: string;
  detail: string;
  tone: 'neutral' | 'success' | 'warn' | 'danger';
} {
  const session = today.latest_session;
  if (!session) {
    return {
      title: 'Film your first swing',
      detail: 'Capture a baseline so your caddie can coach what matters next.',
      tone: 'neutral',
    };
  }
  if (session.status === 'processing' || session.status === 'queued') {
    return {
      title: 'Analysis in progress',
      detail: 'Hang tight — your Brief will appear when coaching is ready.',
      tone: 'warn',
    };
  }
  if (session.status === 'failed') {
    return {
      title: 'Re-film required',
      detail: 'The last capture could not be coached. Try another take.',
      tone: 'danger',
    };
  }
  if (today.practice_checked_in) {
    return {
      title: 'Practice logged',
      detail: 'When you are ready, film a matched re-take of your focus drill.',
      tone: 'success',
    };
  }
  if (today.practice_plan.length > 0) {
    return {
      title: 'Practice your focus drill',
      detail:
        today.practice_plan[0]?.title ??
        today.practice_plan[0]?.drill_name ??
        'Complete today’s practice plan.',
      tone: 'neutral',
    };
  }
  if (today.caddie_brief) {
    return {
      title: 'Review your Caddie Brief',
      detail: 'Open today’s coaching focus before you practice.',
      tone: 'success',
    };
  }
  return {
    title: 'Keep the loop going',
    detail: 'Analyze another swing or revisit Progress.',
    tone: 'neutral',
  };
}

export function TodayScreen({ onAnalyze }: Props) {
  const [status, setStatus] = useState<'loading' | 'error' | 'ready' | 'empty'>(
    'loading',
  );
  const [errorMessage, setErrorMessage] = useState<string | undefined>();
  const [today, setToday] = useState<MobileTodayResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchToday();
        if (cancelled) {
          return;
        }
        setToday(data);
        setStatus('ready');
      } catch (error) {
        if (cancelled) {
          return;
        }
        setStatus('error');
        setErrorMessage(
          error instanceof ApiRequestError
            ? 'Could not load Today. Pull to retry from More shortly.'
            : 'Could not load Today.',
        );
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ScrollScreen accessibilityLabel="Today">
      <Text size="brand" weight="700">
        CaddieInsight
      </Text>
      <Text tone="muted" style={{ marginBottom: space.md }}>
        Today
      </Text>
      <AsyncState status={status} errorMessage={errorMessage}>
        {today ? (
          <View style={{ gap: space.md }}>
            <Card>
              <StatusBadge label={nextAction(today).title} tone={nextAction(today).tone} />
              <Text style={{ marginTop: space.sm }}>{nextAction(today).detail}</Text>
              <View style={{ marginTop: space.md }}>
                <Button label="Analyze a swing" onPress={onAnalyze} />
              </View>
            </Card>
            {today.caddie_brief ? (
              <Card>
                <Text weight="600">Caddie Brief</Text>
                <Text tone="muted" style={{ marginTop: space.sm }}>
                  Coaching is ready for your latest session.
                </Text>
              </Card>
            ) : null}
          </View>
        ) : null}
      </AsyncState>
    </ScrollScreen>
  );
}

export function TodayLoadingShell() {
  return (
    <Screen>
      <Text>Loading Today…</Text>
    </Screen>
  );
}
