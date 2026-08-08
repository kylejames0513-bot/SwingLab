import { useEffect } from 'react';
import * as Linking from 'expo-linking';
import { router } from 'expo-router';

import { hrefForDeepLink, parseDeepLink } from '@/platform/deepLinks';
import { isEnvironmentBoundaryReady } from '@/platform/environmentBoundary';

/**
 * Handles cold/warm deep links after EnvironmentBoundary is ready.
 * Private destinations are route-only; screens refetch owned state.
 */
export function useDeepLinkRouter(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) {
      return;
    }

    function handle(url: string) {
      if (!isEnvironmentBoundaryReady()) {
        return;
      }
      const parsed = parseDeepLink(url);
      const href = hrefForDeepLink(parsed);
      if (href) {
        router.push(href as never);
      }
    }

    void Linking.getInitialURL().then((url) => {
      if (url) {
        handle(url);
      }
    });
    const sub = Linking.addEventListener('url', ({ url }) => handle(url));
    return () => sub.remove();
  }, [enabled]);
}
