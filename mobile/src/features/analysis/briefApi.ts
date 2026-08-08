import { apiRequest } from '@/api/client';
import type { components } from '@/api/schema.generated';

export type BriefResponse = components['schemas']['BriefResponse'];
export type MobileSessionResponse =
  components['schemas']['MobileSessionResponse'];

export async function fetchBrief(sessionId: string): Promise<BriefResponse> {
  return apiRequest<BriefResponse>(
    `/api/v1/mobile/sessions/${sessionId}/brief`,
  );
}

export async function fetchMobileSessions(): Promise<{
  sessions: MobileSessionResponse[];
}> {
  return apiRequest<{ sessions: MobileSessionResponse[] }>(
    '/api/v1/mobile/sessions',
  );
}
