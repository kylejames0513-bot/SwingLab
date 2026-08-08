import { apiRequest } from '@/api/client';

export type DeviceSummary = {
  selector: string;
  label: string;
  created_at?: number;
  last_used_at?: number | null;
  is_current?: boolean;
};

export async function listDevices(): Promise<DeviceSummary[]> {
  const response = await apiRequest<{ devices?: DeviceSummary[] } | DeviceSummary[]>(
    '/api/v1/devices',
  );
  if (Array.isArray(response)) {
    return response;
  }
  return response.devices ?? [];
}

export async function revokeDevice(selector: string): Promise<void> {
  await apiRequest(`/api/v1/devices/${encodeURIComponent(selector)}`, {
    method: 'DELETE',
  });
}
