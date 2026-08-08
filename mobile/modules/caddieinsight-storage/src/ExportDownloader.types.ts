export type ExportDownloadRequest = {
  operationId: string;
  url: string;
  destinationUri: string;
  bearer: string;
  expectedBytes: number;
  generation: number;
};

export type ExportDownloadResult = {
  operationId: string;
  destinationUri: string;
  status: number;
  finalUrl: string;
  contentType: string;
  contentLength: number;
  bytesWritten: number;
  zipSignatureValid: boolean;
};
