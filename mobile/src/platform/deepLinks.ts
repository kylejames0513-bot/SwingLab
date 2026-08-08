/**
 * Allowlisted deep-link destinations only.
 * Private routes must refetch owned state before rendering trusted content.
 */

export type DeepLinkResult =
  | { kind: 'auth_callback'; challengeId: string; code: string }
  | { kind: 'analysis'; sessionId: string }
  | { kind: 'brief'; sessionId: string }
  | { kind: 'help' }
  | { kind: 'rejected'; reason: string };

const OWNED_ID = /^[A-Za-z0-9_-]{1,128}$/;

export function parseDeepLink(url: string): DeepLinkResult {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { kind: 'rejected', reason: 'malformed' };
  }

  const scheme = parsed.protocol.replace(':', '');
  if (
    scheme !== 'caddieinsight' &&
    scheme !== 'caddieinsight-dev' &&
    scheme !== 'caddieinsight-staging' &&
    parsed.protocol !== 'https:'
  ) {
    return { kind: 'rejected', reason: 'scheme' };
  }

  const path = parsed.host
    ? `/${parsed.host}${parsed.pathname}`
    : parsed.pathname;

  // Auth callback: .../app/auth/callback?challenge_id=&code=
  if (path.includes('/app/auth/callback') || path.endsWith('/auth/callback')) {
    const challengeId = parsed.searchParams.get('challenge_id');
    const code = parsed.searchParams.get('code');
    if (!challengeId || !code || !OWNED_ID.test(challengeId)) {
      return { kind: 'rejected', reason: 'auth_params' };
    }
    return { kind: 'auth_callback', challengeId, code };
  }

  const analysis = /\/analysis\/([^/]+)\/?$/.exec(path);
  if (analysis?.[1] && OWNED_ID.test(analysis[1])) {
    return { kind: 'analysis', sessionId: analysis[1] };
  }

  const brief = /\/brief\/([^/]+)\/?$/.exec(path);
  if (brief?.[1] && OWNED_ID.test(brief[1])) {
    return { kind: 'brief', sessionId: brief[1] };
  }

  if (path.includes('/help') || path.endsWith('/support')) {
    return { kind: 'help' };
  }

  return { kind: 'rejected', reason: 'allowlist' };
}

export function hrefForDeepLink(link: DeepLinkResult): string | null {
  switch (link.kind) {
    case 'auth_callback':
      return `/app/auth/callback?challenge_id=${encodeURIComponent(link.challengeId)}&code=${encodeURIComponent(link.code)}`;
    case 'analysis':
      return `/analysis/${link.sessionId}`;
    case 'brief':
      return `/brief/${link.sessionId}`;
    case 'help':
      return '/(tabs)/more';
    default:
      return null;
  }
}
