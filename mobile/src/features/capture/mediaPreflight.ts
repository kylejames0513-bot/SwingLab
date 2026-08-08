import type {
  CaptureContext,
  CapturedMedia,
  PreflightResult,
  UploadCapabilities,
} from './types';

const ALLOWED_DEFAULT: CapturedMedia['suffix'][] = [
  '.avi',
  '.m4v',
  '.mkv',
  '.mov',
  '.mp4',
];

export function suffixFromUri(uri: string): CapturedMedia['suffix'] | null {
  const match = /\.([a-z0-9]+)(?:\?|$)/i.exec(uri);
  if (!match?.[1]) {
    return null;
  }
  const suffix = `.${match[1].toLowerCase()}` as CapturedMedia['suffix'];
  if (!ALLOWED_DEFAULT.includes(suffix)) {
    return null;
  }
  return suffix;
}

export async function preflightMedia(
  media: CapturedMedia,
  capabilities: UploadCapabilities,
  context: CaptureContext,
  options: {
    fileExists: (uri: string) => Promise<boolean>;
    currentComparisonFingerprint: string | null;
  },
): Promise<PreflightResult> {
  if (!(await options.fileExists(media.uri))) {
    return {
      ok: false,
      code: 'missing_file',
      message: 'That video is no longer available on this device.',
    };
  }

  const allowed = capabilities.allowed_suffixes.length
    ? capabilities.allowed_suffixes
    : ALLOWED_DEFAULT;
  if (!allowed.includes(media.suffix)) {
    return {
      ok: false,
      code: 'unsupported_suffix',
      message: 'Use a standard phone video format such as .mp4 or .mov.',
    };
  }

  if (media.sizeBytes > capabilities.max_bytes) {
    return {
      ok: false,
      code: 'oversize',
      message: `Videos must be under ${Math.floor(capabilities.max_bytes / (1024 * 1024))} MB.`,
    };
  }

  if (media.durationSeconds > capabilities.max_video_seconds) {
    return {
      ok: false,
      code: 'overlength',
      message: `Keep the clip under ${capabilities.max_video_seconds} seconds.`,
    };
  }

  if (media.audioExpected === false) {
    return {
      ok: false,
      code: 'audio_required',
      message:
        'Impact audio is part of analysis. Allow microphone access or import a video that includes sound.',
    };
  }

  if (
    context.comparisonFingerprint != null &&
    options.currentComparisonFingerprint != null &&
    context.comparisonFingerprint !== options.currentComparisonFingerprint
  ) {
    return {
      ok: false,
      code: 'comparison_changed',
      message: 'Your Proof Cycle assignment changed. Refresh Today and try again.',
    };
  }

  if (!context.hand || !context.angle || !context.club) {
    return {
      ok: false,
      code: 'invalid_context',
      message: 'Choose hand, camera angle, and club before uploading.',
    };
  }

  return { ok: true, media };
}
