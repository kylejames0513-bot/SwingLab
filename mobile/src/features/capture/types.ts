export type CaptureSource = 'camera' | 'library';

export type CapturedMedia = {
  uri: string;
  sizeBytes: number;
  durationSeconds: number;
  suffix: '.avi' | '.m4v' | '.mkv' | '.mov' | '.mp4';
  mimeType: string;
  source: CaptureSource;
  audioExpected: boolean;
};

export type CaptureContext = {
  hand: 'left' | 'right';
  angle: 'face-on' | 'dtl';
  club: 'driver' | 'fairway-wood' | 'hybrid' | 'iron' | 'wedge';
  comparisonFingerprint: string | null;
};

export type UploadCapabilities = {
  max_bytes: number;
  max_video_seconds: number;
  chunk_bytes: number;
  active_limit: number;
  allowed_suffixes: CapturedMedia['suffix'][];
};

export type PreflightResult =
  | { ok: true; media: CapturedMedia }
  | {
      ok: false;
      code:
        | 'missing_file'
        | 'oversize'
        | 'overlength'
        | 'unsupported_suffix'
        | 'audio_required'
        | 'comparison_changed'
        | 'invalid_context';
      message: string;
    };
