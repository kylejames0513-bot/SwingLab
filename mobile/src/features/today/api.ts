import { apiRequest } from '@/api/client';
import type { components } from '@/api/schema.generated';

export type MobileTodayResponse = components['schemas']['MobileTodayResponse'];

export async function fetchToday(): Promise<MobileTodayResponse> {
  return apiRequest<MobileTodayResponse>('/api/v1/mobile/today');
}
