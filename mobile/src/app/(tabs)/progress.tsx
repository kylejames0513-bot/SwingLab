import { Text, ScrollScreen } from '@/ui/primitives';

export default function ProgressRoute() {
  return (
    <ScrollScreen accessibilityLabel="Progress">
      <Text size="brand" weight="700">
        Progress
      </Text>
      <Text tone="muted">Proof Cycle history will appear here.</Text>
    </ScrollScreen>
  );
}
