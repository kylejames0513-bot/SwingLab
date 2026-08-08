import { router, useLocalSearchParams } from 'expo-router';

import { ProfileForm } from '@/features/profile/ProfileForm';
import { AuthStore } from '@/auth/authStore';
import { PrivateCache } from '@/platform/privateCache';

export default function OnboardingRoute() {
  const params = useLocalSearchParams<{ history_epoch?: string }>();
  const historyEpoch = Number(params.history_epoch ?? '0');

  return (
    <ProfileForm
      historyEpoch={Number.isFinite(historyEpoch) ? historyEpoch : 0}
      onComplete={() => router.replace('/(tabs)/today')}
      onEpochConflict={() => {
        AuthStore.getQueryClient().clear();
        void PrivateCache.clearAll();
        router.replace('/(auth)');
      }}
    />
  );
}
