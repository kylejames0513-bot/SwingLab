import { StyleSheet, Text, View } from 'react-native';

/**
 * Minimal brand shell until feature routes land in later tasks.
 */
export default function IndexScreen() {
  return (
    <View style={styles.container} accessibilityLabel="CaddieInsight home">
      <Text style={styles.brand}>CaddieInsight</Text>
      <Text style={styles.copy}>Coaching client scaffold</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1A3D2E',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  brand: {
    color: '#F4F7F5',
    fontSize: 32,
    fontWeight: '700',
    letterSpacing: 0.4,
  },
  copy: {
    marginTop: 12,
    color: '#B7C9BF',
    fontSize: 16,
  },
});
