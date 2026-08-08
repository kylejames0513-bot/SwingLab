import { preflightMedia, suffixFromUri } from '../../src/features/capture/mediaPreflight';
import type { CapturedMedia, UploadCapabilities } from '../../src/features/capture/types';

const capabilities: UploadCapabilities = {
  max_bytes: 10_000_000,
  max_video_seconds: 20,
  chunk_bytes: 1_000_000,
  active_limit: 1,
  allowed_suffixes: ['.mp4', '.mov'],
};

const baseMedia: CapturedMedia = {
  uri: 'file:///tmp/swing.mp4',
  sizeBytes: 1_000_000,
  durationSeconds: 8,
  suffix: '.mp4',
  mimeType: 'video/mp4',
  source: 'library',
  audioExpected: true,
};

describe('media preflight', () => {
  it('parses allowed suffixes from URIs', () => {
    expect(suffixFromUri('file:///a/b/c.MOV')).toBe('.mov');
    expect(suffixFromUri('file:///a/b/c.webm')).toBeNull();
  });

  it('rejects missing files', async () => {
    const result = await preflightMedia(
      baseMedia,
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: null,
      },
      {
        fileExists: async () => false,
        currentComparisonFingerprint: null,
      },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('missing_file');
    }
  });

  it('rejects oversize, overlength, unsupported suffix, and silent audio', async () => {
    const exists = async () => true;
    const oversize = await preflightMedia(
      { ...baseMedia, sizeBytes: 50_000_000 },
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: null,
      },
      { fileExists: exists, currentComparisonFingerprint: null },
    );
    expect(!oversize.ok && oversize.code).toBe('oversize');

    const overlength = await preflightMedia(
      { ...baseMedia, durationSeconds: 60 },
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: null,
      },
      { fileExists: exists, currentComparisonFingerprint: null },
    );
    expect(!overlength.ok && overlength.code).toBe('overlength');

    const suffix = await preflightMedia(
      { ...baseMedia, suffix: '.mkv' },
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: null,
      },
      { fileExists: exists, currentComparisonFingerprint: null },
    );
    expect(!suffix.ok && suffix.code).toBe('unsupported_suffix');

    const silent = await preflightMedia(
      { ...baseMedia, audioExpected: false },
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: null,
      },
      { fileExists: exists, currentComparisonFingerprint: null },
    );
    expect(!silent.ok && silent.code).toBe('audio_required');
  });

  it('rejects changed comparison context and accepts a valid clip', async () => {
    const changed = await preflightMedia(
      baseMedia,
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: 'aaa',
      },
      {
        fileExists: async () => true,
        currentComparisonFingerprint: 'bbb',
      },
    );
    expect(!changed.ok && changed.code).toBe('comparison_changed');

    const ok = await preflightMedia(
      baseMedia,
      capabilities,
      {
        hand: 'right',
        angle: 'face-on',
        club: 'iron',
        comparisonFingerprint: 'same',
      },
      {
        fileExists: async () => true,
        currentComparisonFingerprint: 'same',
      },
    );
    expect(ok.ok).toBe(true);
  });
});
