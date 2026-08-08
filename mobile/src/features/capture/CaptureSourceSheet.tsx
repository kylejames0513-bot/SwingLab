import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radii, space } from '@/design/tokens';
import type { CaptureSource } from './types';

type Props = {
  onSelect: (source: CaptureSource) => void;
  onCancel: () => void;
  cameraDenied?: boolean;
  libraryDenied?: boolean;
  onOpenSettings?: () => void;
};

export function CaptureSourceSheet({
  onSelect,
  onCancel,
  cameraDenied,
  libraryDenied,
  onOpenSettings,
}: Props) {
  return (
    <View style={styles.root} accessibilityLabel="Choose capture source">
      <Text style={styles.brand}>CaddieInsight</Text>
      <Text style={styles.title}>Analyze a swing</Text>
      <Text style={styles.copy}>
        Camera and import are equal. Permissions are requested only after you choose.
      </Text>
      <Pressable
        style={styles.button}
        onPress={() => onSelect('camera')}
        accessibilityRole="button"
        accessibilityLabel="Record with camera"
      >
        <Text style={styles.buttonText}>Record with camera</Text>
      </Pressable>
      <Pressable
        style={styles.buttonSecondary}
        onPress={() => onSelect('library')}
        accessibilityRole="button"
        accessibilityLabel="Import from library"
      >
        <Text style={styles.buttonSecondaryText}>Import from library</Text>
      </Pressable>
      {(cameraDenied || libraryDenied) && onOpenSettings ? (
        <Pressable
          style={styles.link}
          onPress={onOpenSettings}
          accessibilityRole="button"
          accessibilityLabel="Open settings"
        >
          <Text style={styles.linkText}>Open Settings</Text>
        </Pressable>
      ) : null}
      <Pressable
        style={styles.link}
        onPress={onCancel}
        accessibilityRole="button"
        accessibilityLabel="Cancel"
      >
        <Text style={styles.linkText}>Cancel</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.surfaceDark,
    paddingHorizontal: space.lg,
    paddingTop: 72,
  },
  brand: {
    color: colors.greenInk,
    fontSize: 28,
    fontWeight: '700',
    marginBottom: space.md,
  },
  title: {
    color: colors.bg,
    fontSize: 22,
    fontWeight: '600',
    marginBottom: space.sm,
  },
  copy: {
    color: '#B7C9BF',
    fontSize: 16,
    lineHeight: 22,
    marginBottom: space.lg,
  },
  button: {
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.greenBtn,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: space.sm,
  },
  buttonText: {
    color: colors.greenInk,
    fontSize: 17,
    fontWeight: '600',
  },
  buttonSecondary: {
    minHeight: 48,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.controlBorder,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: space.sm,
  },
  buttonSecondaryText: {
    color: colors.bg,
    fontSize: 17,
    fontWeight: '600',
  },
  link: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: space.sm,
  },
  linkText: {
    color: colors.premiumAccent,
    fontSize: 16,
  },
});
