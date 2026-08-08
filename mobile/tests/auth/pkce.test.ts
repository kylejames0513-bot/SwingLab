import {
  createPKCE,
  normalizeEmail,
  normalizeEmailCode,
} from '../../src/features/auth/pkce';

describe('PKCE and email normalization', () => {
  it('creates S256 verifier/challenge without padding', async () => {
    const { verifier, challenge } = await createPKCE();
    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(challenge).toHaveLength(43);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(challenge.includes('=')).toBe(false);
  });

  it('normalizes grouped email codes to eight digits', () => {
    expect(normalizeEmailCode('1234-5678')).toBe('12345678');
    expect(normalizeEmailCode('1234 5678')).toBe('12345678');
  });

  it('normalizes email casing and trim', () => {
    expect(normalizeEmail('  Kyle@Example.COM ')).toBe('kyle@example.com');
  });
});
