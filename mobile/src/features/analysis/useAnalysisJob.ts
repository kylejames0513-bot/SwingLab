import { useEffect, useState } from 'react';

import { ApiRequestError } from '@/api/errors';
import { fetchMobileSession } from '@/features/analysis/uploadApi';
import type { components } from '@/api/schema.generated';

type MobileSessionResponse = components['schemas']['MobileSessionResponse'];

export function useAnalysisJob(sessionId: string | null) {
  const [session, setSession] = useState<MobileSessionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const next = await fetchMobileSession(sessionId!);
        if (cancelled) {
          return;
        }
        setSession(next);
        setError(null);
        if (next.status === 'queued' || next.status === 'processing') {
          timer = setTimeout(() => void poll(), 2000);
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(
          err instanceof ApiRequestError
            ? 'Could not refresh analysis status.'
            : 'Could not refresh analysis status.',
        );
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [sessionId]);

  return { session, error };
}
