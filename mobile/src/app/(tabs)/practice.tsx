import { PracticeScreen } from '@/features/practice/PracticeScreen';
import { useLocalSearchParams } from 'expo-router';

export default function PracticeRoute() {
  const params = useLocalSearchParams<{
    baseline_session_id?: string;
    target_fingerprint?: string;
    drill_id?: string;
    history_epoch?: string;
  }>();
  return (
    <PracticeScreen
      baselineSessionId={
        typeof params.baseline_session_id === 'string'
          ? params.baseline_session_id
          : ''
      }
      targetFingerprint={
        typeof params.target_fingerprint === 'string'
          ? params.target_fingerprint
          : ''
      }
      drillId={typeof params.drill_id === 'string' ? params.drill_id : ''}
      historyEpoch={Number(params.history_epoch ?? '0')}
    />
  );
}
