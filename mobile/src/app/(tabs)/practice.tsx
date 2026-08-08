import { Text, ScrollScreen } from '@/ui/primitives';

export default function PracticeRoute() {
  return (
    <ScrollScreen accessibilityLabel="Practice">
      <Text size="brand" weight="700">
        Practice
      </Text>
      <Text tone="muted">Your active drill and check-in land here next.</Text>
    </ScrollScreen>
  );
}
