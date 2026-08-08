import { apiRequest } from '@/api/client';
import type { components, operations } from '@/api/schema.generated';

export type Profile = components['schemas']['Profile'];
export type ProfileResponse = components['schemas']['ProfileResponse'];
export type ProfileUpdateBody =
  operations['mobile_resources_profile_write_api_v1_mobile_profile_put']['requestBody']['content']['application/json'];

export async function updateProfile(
  body: ProfileUpdateBody,
): Promise<ProfileResponse> {
  return apiRequest<ProfileResponse>('/api/v1/mobile/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
