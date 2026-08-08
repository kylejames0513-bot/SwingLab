import { type ReactNode } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text as RNText,
  View,
  type TextProps,
  type ViewProps,
} from 'react-native';

import { colors, radii, space } from '@/design/tokens';

export function Screen({
  children,
  style,
  ...rest
}: ViewProps & { children: ReactNode }) {
  return (
    <View style={[styles.screen, style]} {...rest}>
      {children}
    </View>
  );
}

export function ScrollScreen({
  children,
  ...rest
}: { children: ReactNode } & React.ComponentProps<typeof ScrollView>) {
  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.scrollContent}
      {...rest}
    >
      {children}
    </ScrollView>
  );
}

export function Text({
  tone = 'ink',
  size = 'body',
  weight = '400',
  style,
  ...rest
}: TextProps & {
  tone?: 'ink' | 'muted' | 'inverse' | 'accent' | 'danger';
  size?: 'brand' | 'title' | 'body' | 'caption';
  weight?: '400' | '600' | '700';
}) {
  return (
    <RNText
      style={[
        {
          color:
            tone === 'inverse'
              ? colors.greenInk
              : tone === 'muted'
                ? colors.inkMuted
                : tone === 'accent'
                  ? colors.orangeText
                  : tone === 'danger'
                    ? colors.danger
                    : colors.ink,
          fontSize:
            size === 'brand'
              ? 28
              : size === 'title'
                ? 22
                : size === 'caption'
                  ? 13
                  : 16,
          fontWeight: weight,
        },
        style,
      ]}
      {...rest}
    />
  );
}

export function Button({
  label,
  onPress,
  variant = 'primary',
  disabled,
  accessibilityLabel,
}: {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  accessibilityLabel?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      style={[
        styles.button,
        variant === 'secondary' && styles.buttonSecondary,
        variant === 'danger' && styles.buttonDanger,
        disabled && styles.buttonDisabled,
      ]}
    >
      <RNText
        style={[
          styles.buttonLabel,
          variant === 'secondary' && { color: colors.ink },
        ]}
      >
        {label}
      </RNText>
    </Pressable>
  );
}

/** Cards only for interactive/grouped content containers. */
export function Card({
  children,
  style,
}: {
  children: ReactNode;
  style?: ViewProps['style'];
}) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function AsyncState({
  status,
  errorMessage,
  children,
}: {
  status: 'loading' | 'error' | 'ready' | 'empty';
  errorMessage?: string;
  children: ReactNode;
}) {
  if (status === 'loading') {
    return <Text tone="muted">Loading…</Text>;
  }
  if (status === 'error') {
    return <Text tone="danger">{errorMessage ?? 'Something went wrong.'}</Text>;
  }
  if (status === 'empty') {
    return <Text tone="muted">Nothing here yet.</Text>;
  }
  return <>{children}</>;
}

export function StatusBadge({
  label,
  tone = 'neutral',
}: {
  label: string;
  tone?: 'neutral' | 'success' | 'warn' | 'danger';
}) {
  return (
    <View
      style={[
        styles.badge,
        tone === 'success' && { backgroundColor: colors.surfaceSoft },
        tone === 'warn' && { backgroundColor: colors.orangeSoft },
        tone === 'danger' && { backgroundColor: '#F8E8E8' },
      ]}
      accessibilityRole="text"
      accessibilityLabel={`Status: ${label}`}
    >
      <Text size="caption" weight="600">
        {tone === 'success' ? '✓ ' : tone === 'warn' ? '! ' : tone === 'danger' ? '× ' : ''}
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scrollContent: {
    padding: space.lg,
    paddingBottom: space.xl,
  },
  button: {
    minHeight: 48,
    borderRadius: radii.md,
    backgroundColor: colors.greenBtn,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: space.md,
  },
  buttonSecondary: {
    backgroundColor: colors.bgCard,
    borderWidth: 1,
    borderColor: colors.controlBorder,
  },
  buttonDanger: {
    backgroundColor: colors.danger,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  buttonLabel: {
    color: colors.greenInk,
    fontSize: 17,
    fontWeight: '600',
  },
  card: {
    backgroundColor: colors.bgCard,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space.md,
  },
  badge: {
    alignSelf: 'flex-start',
    borderRadius: radii.sm,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: colors.surfaceSoft,
  },
});
