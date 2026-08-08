import { useLocalSearchParams, router } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert } from 'react-native';

import { CaptureSourceSheet } from '@/features/capture/CaptureSourceSheet';
import { GuidedCameraScreen } from '@/features/capture/GuidedCameraScreen';
import { ReviewVideoScreen } from '@/features/capture/ReviewVideoScreen';
import { openSystemSettings, pickLibraryVideo } from '@/features/capture/mediaPicker';
import { preflightMedia } from '@/features/capture/mediaPreflight';
import type {
  CapturedMedia,
  CaptureContext,
  UploadCapabilities,
} from '@/features/capture/types';
import type { UploadComparison } from '@/features/analysis/uploadTypes';
import { getFileAdapter } from '@/platform/files';
import { PrivateCache } from '@/platform/privateCache';

const DEFAULT_CAPABILITIES: UploadCapabilities = {
  max_bytes: 200 * 1024 * 1024,
  max_video_seconds: 30,
  chunk_bytes: 4 * 1024 * 1024,
  active_limit: 2,
  allowed_suffixes: ['.avi', '.m4v', '.mkv', '.mov', '.mp4'],
};

type Phase =
  | { kind: 'source'; cameraDenied?: boolean; libraryDenied?: boolean }
  | { kind: 'camera' }
  | { kind: 'review'; media: CapturedMedia; error?: string };

export default function CaptureRoute() {
  const params = useLocalSearchParams<{
    mode?: string;
    baseline_session_id?: string;
    target_fingerprint?: string;
    drill_id?: string;
    club?: string;
    hand?: string;
    angle?: string;
  }>();

  const comparison: UploadComparison = useMemo(() => {
    if (
      params.mode === 'matched' &&
      params.baseline_session_id &&
      params.target_fingerprint &&
      params.drill_id
    ) {
      return {
        mode: 'matched',
        baseline_session_id: String(params.baseline_session_id),
        target_fingerprint: String(params.target_fingerprint),
        drill_id: String(params.drill_id),
      };
    }
    if (
      params.mode === 'new_context' &&
      params.baseline_session_id &&
      params.target_fingerprint &&
      params.drill_id
    ) {
      return {
        mode: 'new_context',
        baseline_session_id: String(params.baseline_session_id),
        target_fingerprint: String(params.target_fingerprint),
        drill_id: String(params.drill_id),
      };
    }
    return null;
  }, [params]);

  const context: CaptureContext = {
    hand: (params.hand as 'left' | 'right') ?? 'right',
    angle: (params.angle as 'face-on' | 'dtl') ?? 'face-on',
    club:
      (params.club as CaptureContext['club']) ?? 'iron',
    comparisonFingerprint: params.target_fingerprint
      ? String(params.target_fingerprint)
      : null,
  };

  const [phase, setPhase] = useState<Phase>({ kind: 'source' });

  async function handleLibrary() {
    try {
      const picked = await pickLibraryVideo();
      if (picked.status === 'denied') {
        setPhase({ kind: 'source', libraryDenied: true });
        return;
      }
      if (picked.status === 'canceled') {
        setPhase({ kind: 'source' });
        return;
      }
      await reviewMedia(picked.media);
    } catch {
      setPhase({
        kind: 'review',
        media: {
          uri: '',
          sizeBytes: 0,
          durationSeconds: 0,
          suffix: '.mp4',
          mimeType: 'video/mp4',
          source: 'library',
          audioExpected: true,
        },
        error: 'Use a standard phone video format such as .mp4 or .mov.',
      });
    }
  }

  async function reviewMedia(media: CapturedMedia) {
    const result = await preflightMedia(media, DEFAULT_CAPABILITIES, context, {
      fileExists: (uri) => getFileAdapter().exists(uri),
      currentComparisonFingerprint: context.comparisonFingerprint,
    });
    if (!result.ok) {
      setPhase({ kind: 'review', media, error: result.message });
      return;
    }
    const accountId = PrivateCache.getActiveAccountId() ?? 'local';
    await PrivateCache.setActiveAccount(accountId);
    await PrivateCache.writeJson('pending_capture', {
      uri: media.uri,
      source: media.source,
      comparison,
    });
    setPhase({ kind: 'review', media });
  }

  function confirmNewContextThenCapture(source: 'camera' | 'library') {
    if (comparison?.mode === 'matched') {
      Alert.alert(
        'Keep matched context?',
        'Choose Keep matched, or change context deliberately.',
        [
          { text: 'Keep matched', onPress: () => startSource(source) },
          {
            text: 'New context',
            onPress: () => {
              router.setParams({ mode: 'new_context' });
              startSource(source);
            },
          },
          { text: 'Cancel', style: 'cancel' },
        ],
      );
      return;
    }
    startSource(source);
  }

  function startSource(source: 'camera' | 'library') {
    if (source === 'camera') {
      setPhase({ kind: 'camera' });
      return;
    }
    void handleLibrary();
  }

  if (phase.kind === 'camera') {
    return (
      <GuidedCameraScreen
        angle={context.angle}
        maxDurationSeconds={DEFAULT_CAPABILITIES.max_video_seconds}
        onCancel={() => setPhase({ kind: 'source' })}
        onMicrophoneDenied={() =>
          setPhase({ kind: 'source', cameraDenied: true })
        }
        onRecorded={(media) => void reviewMedia(media)}
      />
    );
  }

  if (phase.kind === 'review') {
    return (
      <ReviewVideoScreen
        media={phase.media}
        errorMessage={phase.error}
        onDiscard={() => {
          void PrivateCache.writeJson('pending_capture', null);
          setPhase({ kind: 'source' });
        }}
        onUpload={() => {
          router.push({
            pathname: '/upload',
            params: {
              uri: phase.media.uri,
              sourceName: `swing${phase.media.suffix}`,
              fileBytes: String(phase.media.sizeBytes || 1),
              historyEpoch: '0',
              club: context.club,
              hand: context.hand,
              angle: context.angle,
              comparison: JSON.stringify(comparison),
              chunkBytes: String(DEFAULT_CAPABILITIES.chunk_bytes),
            },
          });
        }}
      />
    );
  }

  return (
    <CaptureSourceSheet
      cameraDenied={phase.cameraDenied}
      libraryDenied={phase.libraryDenied}
      onOpenSettings={() => void openSystemSettings()}
      onCancel={() => router.back()}
      onSelect={(source) => confirmNewContextThenCapture(source)}
    />
  );
}
