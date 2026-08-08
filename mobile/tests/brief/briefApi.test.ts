import { fetchBrief } from '../../src/features/analysis/briefApi';
import { fetchProgress } from '../../src/features/progress/api';
import { configureApiClient, resetApiClient } from '../../src/api/client';
import { deriveAppIdentityHeaders } from '../../src/config/appIdentity';
import { resolveAppEnvironment } from '../../src/config/env';
import { createFixtureFetch } from '../../src/test/server';

describe('coaching loop API routes', () => {
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

  afterEach(() => resetApiClient());

  it('loads Brief only from the mobile brief route', async () => {
    const urls: string[] = [];
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async (req) => {
        urls.push(req.url);
        return {
          status: 200,
          body: {
            resource_version: 1,
            status: 'coaching_ready',
            priority: { name: 'Tempo' },
            evidence: { recurring_sessions: 2, remaining_issues: 1 },
            confidence: 'high',
            prescribed_drill: {
              id: 'd1',
              name: 'Pause',
              aim: 'Smooth',
              dosage: '10 balls',
              pass_mark: 'Center',
            },
            measurement_boundary: {
              club: 'iron',
              hand: 'right',
              angle: 'face-on',
            },
            proof_cycle_target: {
              baseline_session_id: 's1',
              target_fingerprint: 'c'.repeat(64),
              drill_id: 'd1',
              club: 'iron',
              hand: 'right',
              angle: 'face-on',
            },
          },
        };
      }),
      getBearer: async () => 'ciat_x',
    });
    const brief = await fetchBrief('s1');
    expect(urls[0]).toContain('/api/v1/mobile/sessions/s1/brief');
    expect(urls[0]).not.toContain('/api/v1/sessions/');
    expect(brief.status).toBe('coaching_ready');
    expect(brief.proof_cycle_target?.drill_id).toBe('d1');
  });

  it('loads Progress from /api/v1/progress only', async () => {
    const urls: string[] = [];
    configureApiClient({
      baseUrl: env.apiBaseUrl.href,
      identity,
      fetchImpl: createFixtureFetch(async (req) => {
        urls.push(req.url);
        return {
          status: 200,
          body: { resource_version: 1, groups: [] },
        };
      }),
      getBearer: async () => 'ciat_x',
    });
    const progress = await fetchProgress();
    expect(urls[0]).toContain('/api/v1/progress');
    expect(progress.groups).toEqual([]);
  });
});
