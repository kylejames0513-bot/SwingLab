import { StyleSheet, Text, View } from 'react-native';

type Props = {
  angle: 'face-on' | 'dtl';
  countdown?: number | null;
};

export function CaptureOverlay({ angle, countdown }: Props) {
  return (
    <View pointerEvents="none" style={styles.overlay} accessibilityElementsHidden>
      <View style={styles.frame} />
      <Text style={styles.cue}>
        {angle === 'face-on'
          ? 'Face-on: keep the camera level at chest height'
          : 'Down-the-line: align the shaft with the frame edge'}
      </Text>
      <Text style={styles.cueSecondary}>
        Three-swing setup · leave space above the head · impact audio on
      </Text>
      {countdown != null && countdown > 0 ? (
        <Text style={styles.countdown}>{countdown}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFill,
    justifyContent: 'flex-end',
    padding: 24,
  },
  frame: {
    position: 'absolute',
    top: '12%',
    left: '10%',
    right: '10%',
    bottom: '28%',
    borderWidth: 2,
    borderColor: 'rgba(233, 242, 236, 0.55)',
    borderRadius: 18,
  },
  cue: {
    color: '#E9F2EC',
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 6,
  },
  cueSecondary: {
    color: '#B7C9BF',
    fontSize: 14,
    marginBottom: 16,
  },
  countdown: {
    position: 'absolute',
    alignSelf: 'center',
    top: '40%',
    color: '#FFAD62',
    fontSize: 64,
    fontWeight: '700',
  },
});
