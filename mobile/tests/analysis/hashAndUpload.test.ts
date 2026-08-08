import { hashAndUpload } from '../../src/features/analysis/hashAndUpload';
import { configureApiClient, resetApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import {
  createMemorySecureStoreAdapter,
  resetSecureStoreAdapter,
  setSecureStoreAdapter,
} from '../../src/platform/secureStore';
import { resetPrivateCacheBackend } from '../../src/platform/privateCache';
import { createFixtureFetch, headerMapLower } from '../../src/test/server';

describe('hashAndUpload transport', () => {
  const env = resolveAppEnvironment({
    appEnv: 'development',
    apiBaseUrl: 'https://api.example.com',
    env: {},
  });
  const identity = deriveAppIdentityHeaders({
    environment: env,
    platform: 'ios',
    appVersion: '1.0.0',
    appBuild: '1',
    applicationId: 'com.caddieinsight.app.dev',
  });

  beforeEach(() => {
    setSecureStoreAdapter(createMemorySecureStoreAdapter());
    resetPrivateCacheBackend();
    resetApiClient();
  });

  afterEach(() => {
    resetSecureStoreAdapter();
    resetPrivateCacheBackend();
    resetApiClient();
  });

  it('sends exact offset/checksum headers per chunk and completes', async () => {
    const chunks: Array<{ offset: string; checksum: string; length: number }> = [];
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async (req) => {
        const headers = headerMapLower(req.headers);
        if (req.method === 'POST' && req.url.endsWith('/api/v1/uploads')) {
          return {
            status: 201,
            body: {
              resource_version: 1,
              upload_id: 'up-1',
              status: 'pending',
              offset: 0,
              file_bytes: 8,
              chunk_bytes: 4,
              expires_at: 9,
            },
          };
        }
        if (req.method === 'PATCH') {
          chunks.push({
            offset: headers['upload-offset'] ?? '',
            checksum: headers['upload-checksum'] ?? '',
            length: req.body ? JSON.stringify(req.body).length : 0,
          });
          const offset = Number(headers['upload-offset'] ?? '0') + 4;
          return {
            status: 200,
            body: {
              resource_version: 1,
              upload_id: 'up-1',
              status: 'pending',
              offset: Math.min(offset, 8),
              file_bytes: 8,
              chunk_bytes: 4,
              expires_at: 9,
            },
          };
        }
        if (req.url.includes('/complete')) {
          return {
            status: 200,
            body: { resource_version: 1, session_id: 'sess-1' },
          };
        }
        return { status: 404, body: { code: 'not_found', message: 'no' } };
      }),
      getBearer: async () => 'ciat_test',
    });

    const payload = Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8]);
    const generator = hashAndUpload({
      localUri: 'file:///tmp/swing.mp4',
      sourceName: 'swing.mp4',
      fileBytes: 8,
      accountId: 'user-1',
      historyEpoch: 1,
      club: 'iron',
      hand: 'right',
      angle: 'face-on',
      comparison: null,
      chunkBytes: 4,
      idempotencyKey: '0123456789abcdef0123456789abcdef',
      hashFile: async () => 'b'.repeat(64),
      hashChunk: async () => 'AAAA',
      readChunk: async (_uri, offset, chunkLength) =>
        payload.slice(offset, offset + chunkLength),
    });

    let final = await generator.next();
    while (!final.done) {
      final = await generator.next();
    }
    expect(final.value.state).toBe('queued');
    expect(final.value.sessionId).toBe('sess-1');
    expect(chunks.length).toBe(2);
    expect(chunks[0]?.offset).toBe('0');
    expect(chunks[1]?.offset).toBe('4');
    expect(chunks[0]?.checksum).toBe('AAAA');
  });
});
