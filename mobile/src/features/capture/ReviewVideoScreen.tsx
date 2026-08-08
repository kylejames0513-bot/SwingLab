import { useVideoPlayer, VideoView } from 'expo-video';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { CapturedMedia } from './types';

type Props = {
  media: CapturedMedia;
  errorMessage?: string | null;
  onDiscard: () => void;
  onUpload: () => void;
};

export function ReviewVideoScreen({
  media,
  errorMessage,
  onDiscard,
  onUpload,
}: Props) {
  const player = useVideoPlayer(media.uri, (instance) => {
    instance.loop = true;
  });

  return (
    <View style={styles.root} accessibilityLabel="Review swing video">
      <Text style={styles.brand}>CaddieInsight</Text>
      <Text style={styles.title}>Review before upload</Text>
      <VideoView
        style={styles.video}
        player={player}
        nativeControls
      />
      <Text style={styles.meta}>
        {media.source === 'camera' ? 'Camera' : 'Import'} · {media.suffix} ·{' '}
        {Math.round(media.durationSeconds)}s
      </Text>
      {errorMessage ? <Text style={styles.error}>{errorMessage}</Text> : null}
      <Pressable
        style={styles.primary}
        onPress={onUpload}
        accessibilityRole="button"
        accessibilityLabel="Upload swing"
      >
        <Text style={styles.primaryText}>Upload</Text>
      </Pressable>
      <Pressable
        style={styles.link}
        onPress={onDiscard}
        accessibilityRole="button"
        accessibilityLabel="Discard video"
      >
        <Text style={styles.linkText}>Discard</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#103C27',
    paddingHorizontal: 24,
    paddingTop: 64,
  },
  brand: { color: '#E9F2EC', fontSize: 28, fontWeight: '700', marginBottom: 12 },
  title: { color: '#F7F5F0', fontSize: 22, fontWeight: '600', marginBottom: 16 },
  video: { width: '100%', height: 280, borderRadius: 14, backgroundColor: '#000' },
  meta: { color: '#B7C9BF', marginTop: 12, marginBottom: 12 },
  error: { color: '#F2C6C2', marginBottom: 12 },
  primary: {
    minHeight: 48,
    borderRadius: 14,
    backgroundColor: '#1A5C38',
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryText: { color: '#E9F2EC', fontSize: 17, fontWeight: '600' },
  link: { minHeight: 44, alignItems: 'center', justifyContent: 'center', marginTop: 12 },
  linkText: { color: '#FFAD62', fontSize: 16 },
});
