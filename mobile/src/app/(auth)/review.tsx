import { ReviewAccessScreen } from '@/features/auth/ReviewAccessScreen';
import { router } from 'expo-router';

export default function ReviewAuthRoute() {
  return (
    <ReviewAccessScreen
      onCancel={() => router.replace('/(auth)')}
      onSignedIn={() => router.replace('/(tabs)/today')}
    />
  );
}
