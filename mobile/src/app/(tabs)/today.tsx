import { router } from 'expo-router';

import { TodayScreen } from '@/features/today/TodayScreen';

export default function TodayRoute() {
  return <TodayScreen onAnalyze={() => router.push('/capture')} />;
}
