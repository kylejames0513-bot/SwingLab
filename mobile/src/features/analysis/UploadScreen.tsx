import { useEffect, useRef, useState } from 'react';
import { router } from 'expo-router';

import { ApiRequestError } from '@/api/errors';
import { createIdempotencyKey } from '@/features/auth/api';
import { hashAndUpload } from '@/features/analysis/hashAndUpload';
import { uploadRepository } from '@/features/analysis/uploadRepository';
import type {
  UploadComparison,
  UploadProgress,
} from '@/features/analysis/uploadTypes';
import { subscribeForegroundUploadPolicy } from '@/platform/appLifecycle';
import { PrivateCache } from '@/platform/privateCache';
import { Button, ScrollScreen, StatusBadge, Text } from '@/ui/primitives';
import { space } from '@/design/tokens';

export type UploadScreenProps = {
  localUri: string;
  sourceName: string;
  fileBytes: number;
  historyEpoch: number;
  club: 'driver' | 'fairway-wood' | 'hybrid' | 'iron' | 'wedge';
  hand: 'left' | 'right';
  angle: 'face-on' | 'dtl';
  comparison: UploadComparison;
  chunkBytes: number;
};

export function UploadScreen(props: UploadScreenProps) {
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [doneSessionId, setDoneSessionId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runningRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      if (runningRef.current) {
        return;
      }
      runningRef.current = true;
      const accountId = PrivateCache.getActiveAccountId() ?? 'local';
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const idempotencyKey = await createIdempotencyKey();
        const generator = hashAndUpload({
          ...props,
          accountId,
          idempotencyKey,
          signal: controller.signal,
        });
        let result = await generator.next();
        while (!result.done) {
          if (cancelled) {
            return;
          }
          setProgress(result.value);
          result = await generator.next();
        }
        if (!cancelled) {
          setProgress({
            uploadId: result.value.uploadId ?? '',
            offset: result.value.offset,
            fileBytes: result.value.fileBytes,
            state: result.value.state,
          });
          if (result.value.sessionId) {
            setDoneSessionId(result.value.sessionId);
          }
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiRequestError) {
          setError('Upload could not finish. You can resume or discard.');
        } else if (err instanceof Error && err.name === 'AbortError') {
          setError('Upload paused. Return to the app to resume.');
        } else {
          setError('Upload could not finish. You can resume or discard.');
        }
      } finally {
        runningRef.current = false;
      }
    }

    void run();
    const unsubscribe = subscribeForegroundUploadPolicy({
      onBackground: () => {
        abortRef.current?.abort();
      },
      onForeground: () => {
        // Resume is explicit via button after pause to avoid duplicate loops.
      },
    });

    return () => {
      cancelled = true;
      abortRef.current?.abort();
      unsubscribe();
    };
  }, [props]);

  const percent =
    progress && progress.fileBytes > 0
      ? Math.min(100, Math.round((progress.offset / progress.fileBytes) * 100))
      : 0;

  return (
    <ScrollScreen accessibilityLabel="Upload progress">
      <Text size="brand" weight="700">
        CaddieInsight
      </Text>
      <Text size="title" weight="600" style={{ marginTop: space.sm }}>
        Uploading swing
      </Text>
      <Text tone="muted" style={{ marginVertical: space.md }}>
        Transfer pauses if you leave the app. Foreground resume reconciles the
        server offset first.
      </Text>
      {progress ? (
        <StatusBadge
          label={`${progress.state} · ${percent}%`}
          tone={progress.state === 'queued' ? 'success' : 'warn'}
        />
      ) : (
        <Text tone="muted">Preparing…</Text>
      )}
      {error ? (
        <Text tone="danger" style={{ marginTop: space.md }}>
          {error}
        </Text>
      ) : null}
      {doneSessionId ? (
        <Button
          label="View analysis"
          onPress={() => router.replace(`/analysis/${doneSessionId}`)}
        />
      ) : null}
      <Button
        label="Discard upload"
        variant="danger"
        onPress={() => {
          abortRef.current?.abort();
          void uploadRepository.clear();
          router.replace('/(tabs)/today');
        }}
      />
    </ScrollScreen>
  );
}
