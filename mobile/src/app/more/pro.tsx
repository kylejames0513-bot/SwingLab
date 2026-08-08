import { ScrollScreen, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';

/** Purchase UI is Plan 3; Version 1 shows provider-aware summary only. */
export default function ProRoute() {
  return (
    <ScrollScreen accessibilityLabel="Pro">
      <Text size="brand" weight="700">
        Pro
      </Text>
      <Text tone="muted" style={{ marginTop: space.md }}>
        Native billing lands in the entitlements plan. This screen summarizes
        server quota/plan only and never offers web checkout for digital Pro.
      </Text>
    </ScrollScreen>
  );
}
