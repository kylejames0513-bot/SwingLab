import { QueryClient } from '@tanstack/react-query';

import { ApiRequestError } from './errors';

export type HistoryEpoch = number;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        networkMode: 'online',
        retry: (failureCount, error) => {
          if (failureCount >= 2) {
            return false;
          }
          if (error instanceof ApiRequestError) {
            return error.appError.retryable && error.appError.status !== 401;
          }
          return false;
        },
        staleTime: 30_000,
        gcTime: 5 * 60_000,
      },
      mutations: {
        networkMode: 'online',
        retry: false,
      },
    },
  });
}

/** Query keys are always scoped by history_epoch for private current truth. */
export function privateQueryKey(
  historyEpoch: HistoryEpoch,
  ...parts: readonly unknown[]
): readonly unknown[] {
  return ['private', historyEpoch, ...parts] as const;
}

export function meQueryKey(historyEpoch: HistoryEpoch = 0): readonly unknown[] {
  return privateQueryKey(historyEpoch, 'me');
}
