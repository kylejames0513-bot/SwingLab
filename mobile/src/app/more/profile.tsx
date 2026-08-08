import { router } from 'expo-router';

import { ProfileForm } from '@/features/profile/ProfileForm';
import { AuthStore } from '@/auth/authStore';
import { PrivateCache } from '@/platform/privateCache';

export default function MoreProfileRoute() {
  return (
    <ProfileForm
      historyEpoch={0}
      onComplete={() => router.back()}
      onEpochConflict={() => {
        AuthStore.getQueryClient().clear();
        void PrivateCache.clearAll();
        router.replace('/(auth)');
      }}
    />
  );
}
