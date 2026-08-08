import { Redirect } from 'expo-router';

import { AuthStore } from '@/auth/authStore';

export default function Index() {
  const state = AuthStore.getState();
  if (state.status === 'signed_in') {
    return <Redirect href="/(tabs)/today" />;
  }
  return <Redirect href="/(auth)" />;
}
