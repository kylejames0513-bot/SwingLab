import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { Platform } from 'react-native';

import { apiRequest, apiRequestWithStatus } from '@/api/client';
import { getAppEnvironment } from '@/config/env';
import { registerEnvironmentPurgeHook } from '@/platform/environmentBoundary';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export type PushRegistrationBody = {
  provider: 'expo';
  token: string;
  platform: 'ios' | 'android';
  app_version: string;
  expo_project_id: string;
  practice_reminders_enabled: boolean;
};

let practiceRemindersEnabled = false;
let lastToken: string | null = null;

export function getPracticeRemindersEnabled(): boolean {
  return practiceRemindersEnabled;
}

export async function requestNotificationPermission(): Promise<boolean> {
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) {
    return true;
  }
  const asked = await Notifications.requestPermissionsAsync();
  return asked.granted;
}

export async function registerForPushAfterOptIn(options: {
  appVersion: string;
  practiceRemindersEnabled?: boolean;
}): Promise<'registered' | 'denied' | 'skipped'> {
  const granted = await requestNotificationPermission();
  if (!granted) {
    return 'denied';
  }

  const env = getAppEnvironment();
  const projectId =
    env.easProjectId ??
    Constants.easConfig?.projectId ??
    Constants.expoConfig?.extra?.eas?.projectId ??
    null;
  if (!projectId || typeof projectId !== 'string') {
    return 'skipped';
  }

  const tokenResponse = await Notifications.getExpoPushTokenAsync({
    projectId,
  });
  lastToken = tokenResponse.data;
  practiceRemindersEnabled = options.practiceRemindersEnabled ?? practiceRemindersEnabled;

  const body: PushRegistrationBody = {
    provider: 'expo',
    token: tokenResponse.data,
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
    app_version: options.appVersion,
    expo_project_id: projectId,
    practice_reminders_enabled: practiceRemindersEnabled,
  };

  await apiRequest('/api/v1/devices/push', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return 'registered';
}

export async function refreshPushRegistration(appVersion: string): Promise<void> {
  if (!lastToken) {
    return;
  }
  await registerForPushAfterOptIn({
    appVersion,
    practiceRemindersEnabled,
  });
}

export async function setPracticeReminders(enabled: boolean): Promise<void> {
  practiceRemindersEnabled = enabled;
  await apiRequest('/api/v1/devices/push/preferences', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ practice_reminders_enabled: enabled }),
  });
}

export async function unregisterPush(): Promise<void> {
  try {
    await apiRequestWithStatus('/api/v1/devices/push', { method: 'DELETE' });
  } catch {
    // Sign-out continues even if push unregister is pending.
  }
  lastToken = null;
}

export async function clearLocalNotifications(): Promise<void> {
  await Notifications.dismissAllNotificationsAsync();
  await Notifications.cancelAllScheduledNotificationsAsync();
  if (typeof Notifications.clearLastNotificationResponseAsync === 'function') {
    await Notifications.clearLastNotificationResponseAsync();
  }
}

registerEnvironmentPurgeHook(() => clearLocalNotifications());

export function parseNotificationRoute(data: Record<string, unknown>): string | null {
  const route = data.route;
  if (typeof route !== 'string') {
    return null;
  }
  return route;
}
