import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import * as Haptics from 'expo-haptics';

import { OrientationController } from '@/platform/orientation';
import { CaptureOverlay } from './CaptureOverlay';
import type { CapturedMedia } from './types';
import { suffixFromUri } from './mediaPreflight';

type Props = {
  angle: 'face-on' | 'dtl';
  maxDurationSeconds: number;
  onRecorded: (media: CapturedMedia) => void;
  onCancel: () => void;
  onMicrophoneDenied: () => void;
};

export function GuidedCameraScreen({
  angle,
  maxDurationSeconds,
  onRecorded,
  onCancel,
  onMicrophoneDenied,
}: Props) {
  const cameraRef = useRef<CameraView>(null);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [micPermission, requestMicPermission] = useMicrophonePermissions();
  const [recording, setRecording] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);

  useEffect(() => {
    void OrientationController.enterCapture();
    return () => {
      void OrientationController.leaveCapture();
    };
  }, []);

  async function startRecording() {
    if (!cameraPermission?.granted) {
      const next = await requestCameraPermission();
      if (!next.granted) {
        onCancel();
        return;
      }
    }
    const mic = micPermission?.granted
      ? micPermission
      : await requestMicPermission();
    if (!mic.granted) {
      onMicrophoneDenied();
      return;
    }

    setCountdown(3);
    for (let i = 3; i >= 1; i -= 1) {
      setCountdown(i);
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(
        () => undefined,
      );
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    setCountdown(null);

    const camera = cameraRef.current;
    if (!camera) {
      return;
    }
    setRecording(true);
    try {
      const video = await camera.recordAsync({
        maxDuration: maxDurationSeconds,
      });
      if (!video?.uri) {
        return;
      }
      const suffix = suffixFromUri(video.uri) ?? '.mp4';
      onRecorded({
        uri: video.uri,
        sizeBytes: 0,
        durationSeconds: maxDurationSeconds,
        suffix,
        mimeType: 'video/mp4',
        source: 'camera',
        audioExpected: true,
      });
    } finally {
      setRecording(false);
      void OrientationController.leaveCapture();
    }
  }

  async function stopRecording() {
    cameraRef.current?.stopRecording();
  }

  return (
    <View style={styles.root} accessibilityLabel="Guided camera">
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        mode="video"
        mute={false}
        barcodeScannerSettings={undefined}
      />
      <CaptureOverlay angle={angle} countdown={countdown} />
      <View style={styles.controls}>
        <Pressable
          style={styles.secondary}
          onPress={() => {
            void OrientationController.leaveCapture();
            onCancel();
          }}
          accessibilityRole="button"
          accessibilityLabel="Cancel recording"
        >
          <Text style={styles.secondaryText}>Cancel</Text>
        </Pressable>
        <Pressable
          style={styles.record}
          onPress={() => void (recording ? stopRecording() : startRecording())}
          accessibilityRole="button"
          accessibilityLabel={recording ? 'Stop recording' : 'Start recording'}
        >
          <Text style={styles.recordText}>{recording ? 'Stop' : 'Record'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#000' },
  controls: {
    position: 'absolute',
    bottom: 36,
    left: 24,
    right: 24,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  secondary: {
    minHeight: 48,
    minWidth: 88,
    alignItems: 'center',
    justifyContent: 'center',
  },
  secondaryText: { color: '#E9F2EC', fontSize: 16, fontWeight: '600' },
  record: {
    minHeight: 56,
    minWidth: 112,
    borderRadius: 28,
    backgroundColor: '#1A5C38',
    alignItems: 'center',
    justifyContent: 'center',
  },
  recordText: { color: '#E9F2EC', fontSize: 17, fontWeight: '700' },
});
