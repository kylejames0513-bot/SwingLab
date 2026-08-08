import { Text, ScrollScreen } from '@/ui/primitives';

export default function AnalyzeRoute() {
  return (
    <ScrollScreen accessibilityLabel="Analyze">
      <Text size="brand" weight="700">
        Analyze
      </Text>
      <Text tone="muted">Choose camera or import to start a swing capture.</Text>
    </ScrollScreen>
  );
}
