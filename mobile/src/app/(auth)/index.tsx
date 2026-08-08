import { EmailSignInScreen } from '@/features/auth/EmailSignInScreen';
import { router } from 'expo-router';

export default function AuthIndex() {
  return (
    <EmailSignInScreen
      onOpenReviewAccess={() => router.push('/(auth)/review')}
      onSignedIn={() => router.replace('/(tabs)/today')}
    />
  );
}
