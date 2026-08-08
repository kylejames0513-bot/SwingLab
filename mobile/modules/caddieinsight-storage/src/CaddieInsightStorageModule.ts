import type {
  ExportDownloadRequest,
  ExportDownloadResult,
} from './ExportDownloader.types';

export type ProtectedRoots = {
  pendingUploadsDirectory: string;
  stateDirectory: string;
  exportTemporaryDirectory: string;
};

/**
 * Native bridge stub. Real iOS/Android implementations live under ios/ and android/.
 * Until those are linked in a development build, calls fail closed.
 */
type CaddieInsightStorageModuleNative = {
  ensureProtectedRoots(): Promise<ProtectedRoots>;
  protectAndVerify(uri: string): Promise<void>;
  downloadExport(request: ExportDownloadRequest): Promise<ExportDownloadResult>;
  cancelAndDrain(operationId: string): Promise<void>;
};

const UNSUPPORTED =
  'CaddieInsightStorage native module is not linked. Use a development build; web/tests fail closed.';

const CaddieInsightStorageModule: CaddieInsightStorageModuleNative = {
  async ensureProtectedRoots(): Promise<ProtectedRoots> {
    throw new Error(UNSUPPORTED);
  },
  async protectAndVerify(_uri: string): Promise<void> {
    throw new Error(UNSUPPORTED);
  },
  async downloadExport(
    _request: ExportDownloadRequest,
  ): Promise<ExportDownloadResult> {
    throw new Error(UNSUPPORTED);
  },
  async cancelAndDrain(_operationId: string): Promise<void> {
    throw new Error(UNSUPPORTED);
  },
};

export default CaddieInsightStorageModule;
