import { Text, ScrollScreen } from '@/ui/primitives';

/** Capture entry placeholder until Task 5 guided camera lands. */
export default function CaptureRoute() {
  return (
    <ScrollScreen accessibilityLabel="Capture">
      <Text size="brand" weight="700">
        Capture
      </Text>
      <Text tone="muted">
        Guided camera and import land in the next slice. Analyze remains available
        from the center tab.
      </Text>
    </ScrollScreen>
  );
}
