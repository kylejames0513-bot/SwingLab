import { useLocalSearchParams } from 'expo-router';

import { BriefScreen } from '@/features/analysis/BriefScreen';
import { Text, ScrollScreen } from '@/ui/primitives';

export default function BriefRoute() {
  const params = useLocalSearchParams<{ sessionId?: string }>();
  const sessionId =
    typeof params.sessionId === 'string' ? params.sessionId : null;
  if (!sessionId) {
    return (
      <ScrollScreen>
        <Text>Missing session.</Text>
      </ScrollScreen>
    );
  }
  return <BriefScreen sessionId={sessionId} />;
}
