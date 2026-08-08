import { AppState, type AppStateStatus, type NativeEventSubscription } from 'react-native';

export type ForegroundUploadPolicyHandlers = {
  onBackground: () => void;
  onForeground: () => void;
};

/**
 * Version 1: background/inactive aborts the in-flight chunk and pauses.
 * Foreground must reconcile server offset before resume.
 */
export function subscribeForegroundUploadPolicy(
  handlers: ForegroundUploadPolicyHandlers,
): () => void {
  let current: AppStateStatus = AppState.currentState;
  const sub: NativeEventSubscription = AppState.addEventListener(
    'change',
    (next) => {
      const wasActive = current === 'active';
      const nowActive = next === 'active';
      current = next;
      if (wasActive && !nowActive) {
        handlers.onBackground();
      } else if (!wasActive && nowActive) {
        handlers.onForeground();
      }
    },
  );
  return () => sub.remove();
}
