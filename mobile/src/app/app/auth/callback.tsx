import { useLocalSearchParams, router } from 'expo-router';

import { AuthCallbackScreen } from '@/features/auth/AuthCallbackScreen';

export default function AuthCallbackRoute() {
  const params = useLocalSearchParams<{
    challenge_id?: string;
    code?: string;
  }>();

  return (
    <AuthCallbackScreen
      challengeId={
        typeof params.challenge_id === 'string' ? params.challenge_id : null
      }
      emailCode={typeof params.code === 'string' ? params.code : null}
      onRestart={() => router.replace('/(auth)')}
      onSignedIn={() => router.replace('/(tabs)/today')}
    />
  );
}
