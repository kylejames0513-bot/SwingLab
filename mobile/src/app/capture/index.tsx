import { useState } from 'react';
import { router } from 'expo-router';

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

const DEFAULT_CONTEXT: CaptureContext = {
  hand: 'right',
  angle: 'face-on',
  club: 'iron',
  comparisonFingerprint: null,
};

export default function CaptureRoute() {
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
    const result = await preflightMedia(
      media,
      DEFAULT_CAPABILITIES,
      DEFAULT_CONTEXT,
      {
        fileExists: (uri) => getFileAdapter().exists(uri),
        currentComparisonFingerprint: null,
      },
    );
    if (!result.ok) {
      setPhase({ kind: 'review', media, error: result.message });
      return;
    }
    const accountId = PrivateCache.getActiveAccountId() ?? 'local';
    await PrivateCache.setActiveAccount(accountId);
    await PrivateCache.writeJson('pending_capture', {
      uri: media.uri,
      source: media.source,
    });
    setPhase({ kind: 'review', media });
  }

  if (phase.kind === 'camera') {
    return (
      <GuidedCameraScreen
        angle={DEFAULT_CONTEXT.angle}
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
          // Task 6 wires the resumable upload machine.
          router.push('/(tabs)/today');
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
      onSelect={(source) => {
        if (source === 'camera') {
          setPhase({ kind: 'camera' });
          return;
        }
        void handleLibrary();
      }}
    />
  );
}
