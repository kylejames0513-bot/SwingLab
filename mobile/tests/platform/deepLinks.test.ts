import {
  hrefForDeepLink,
  parseDeepLink,
} from '../../src/platform/deepLinks';

describe('deepLinks', () => {
  it('parses auth callbacks', () => {
    const link = parseDeepLink(
      'caddieinsight://app/auth/callback?challenge_id=chal-1&code=1234-5678',
    );
    expect(link.kind).toBe('auth_callback');
    if (link.kind === 'auth_callback') {
      expect(link.challengeId).toBe('chal-1');
      expect(link.code).toBe('1234-5678');
    }
  });

  it('parses owned analysis and brief routes', () => {
    expect(parseDeepLink('caddieinsight://analysis/sess_1')).toEqual({
      kind: 'analysis',
      sessionId: 'sess_1',
    });
    expect(parseDeepLink('https://app.example.com/brief/sess_2')).toEqual({
      kind: 'brief',
      sessionId: 'sess_2',
    });
  });

  it('rejects unknown and malformed links', () => {
    expect(parseDeepLink('https://evil.example/steal').kind).toBe('rejected');
    expect(parseDeepLink('not a url').kind).toBe('rejected');
  });

  it('builds safe hrefs', () => {
    expect(
      hrefForDeepLink({ kind: 'analysis', sessionId: 'abc' }),
    ).toBe('/analysis/abc');
  });
});
