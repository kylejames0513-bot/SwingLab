import {
  canTransition,
  createPendingUpload,
  reconcileUpload,
  transition,
} from '../../src/features/analysis/uploadMachine';
import { uploadRepository } from '../../src/features/analysis/uploadRepository';
import {
  createMemorySecureStoreAdapter,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
} from '../../src/platform/secureStore';
import {
  resetPrivateCacheBackend,
} from '../../src/platform/privateCache';

function sample() {
  return createPendingUpload({
    localUri: 'file:///tmp/a.mp4',
    sourceName: 'a.mp4',
    fileSha256: 'a'.repeat(64),
    fileBytes: 1000,
    idempotencyKey: '0123456789abcdef0123456789abcdef',
    abortIdempotencyKey: null,
    accountId: 'user-1',
    historyEpoch: 2,
    comparison: null,
    club: 'iron',
    hand: 'right',
    angle: 'face-on',
    chunkBytes: 256,
  });
}

describe('uploadMachine', () => {
  it('allows preparing -> reserving -> uploading -> verifying -> queued', () => {
    let upload = sample();
    upload = transition(upload, 'reserving');
    upload = transition(upload, 'uploading', { uploadId: 'u1', offset: 0 });
    upload = transition(upload, 'verifying', { offset: 1000 });
    upload = transition(upload, 'queued');
    expect(upload.state).toBe('queued');
  });

  it('rejects illegal transitions', () => {
    const upload = sample();
    expect(() => transition(upload, 'done')).toThrow(/Illegal/);
    expect(canTransition('done', 'uploading')).toBe(false);
  });

  it('reconciles with authoritative server offset without moving backward', () => {
    let upload = transition(sample(), 'reserving');
    upload = transition(upload, 'uploading', { uploadId: 'u1', offset: 100 });
    const reconciled = reconcileUpload(upload, {
      upload_id: 'u1',
      offset: 250,
      status: 'pending',
    });
    expect(reconciled.offset).toBe(250);
    expect(reconciled.state).toBe('uploading');
  });

  it('pauses from uploading and resumes only via allowed transition', () => {
    let upload = transition(sample(), 'reserving');
    upload = transition(upload, 'uploading', { uploadId: 'u1' });
    upload = transition(upload, 'paused');
    expect(upload.state).toBe('paused');
    upload = transition(upload, 'uploading');
    expect(upload.state).toBe('uploading');
  });
});

describe('uploadRepository', () => {
  beforeEach(() => {
    setSecureStoreAdapter(createMemorySecureStoreAdapter());
    resetPrivateCacheBackend();
  });
  afterEach(() => {
    resetSecureStoreAdapter();
    resetPrivateCacheBackend();
  });

  it('persists pending upload without a bearer field', async () => {
    const upload = transition(sample(), 'reserving');
    await uploadRepository.save(upload);
    const loaded = await uploadRepository.load();
    expect(loaded?.accountId).toBe('user-1');
    expect(JSON.stringify(loaded)).not.toMatch(/ciat_|bearer|Authorization/i);
  });
});
