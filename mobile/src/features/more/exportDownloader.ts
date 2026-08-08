import { getAppEnvironment } from '@/config/env';
import {
  MAX_PRIVACY_EXPORT_ZIP_BYTES,
} from '@/features/more/privacy';

export type ReadyExportReceipt = {
  export_id: string;
  status: 'ready';
  byte_size: number;
  max_download_bytes: number;
};

/**
 * JS facade for native streaming export download.
 * Never pulls ZIP bytes into JavaScript.
 */
export async function downloadReadyExport(
  receipt: ReadyExportReceipt,
  _bearer: string,
): Promise<{ destinationUri: string; bytesWritten: number }> {
  if (receipt.status !== 'ready') {
    throw new Error('Export is not ready.');
  }
  if (receipt.max_download_bytes !== MAX_PRIVACY_EXPORT_ZIP_BYTES) {
    throw new Error('Export max bytes drifted from the OpenAPI contract.');
  }
  if (
    !Number.isInteger(receipt.byte_size) ||
    receipt.byte_size < 1 ||
    receipt.byte_size > MAX_PRIVACY_EXPORT_ZIP_BYTES
  ) {
    throw new Error('Export byte_size is out of range.');
  }

  const env = getAppEnvironment();
  const url = `${env.apiOrigin}/api/v1/privacy/exports/${encodeURIComponent(receipt.export_id)}/download`;
  // Native module streams; this environment fails closed until device build.
  throw new Error(
    `Native export download required for ${url}. Use a development build with caddieinsight-storage.`,
  );
}

/** Lint/source guard targets — do not call these for exports. */
export const EXPORT_DOWNLOAD_BANNED_APIS = [
  'File.downloadFileAsync',
  'expo/fetch',
  'arrayBuffer',
  'bytes',
  'base64',
  'FormData',
] as const;
