import { apiRequest } from '@/api/client';
import type { components } from '@/api/schema.generated';

export type ProgressResponse = components['schemas']['ProgressResponse'];

export async function fetchProgress(): Promise<ProgressResponse> {
  return apiRequest<ProgressResponse>('/api/v1/progress');
}
