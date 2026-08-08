import * as WebBrowser from 'expo-web-browser';

/**
 * Physical gear opens only the server-configured HTTPS Shopify host.
 * Never injects bearer/cookies into the system browser.
 */
export async function openGearStore(url: string): Promise<void> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error('Gear store URL is invalid.');
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('Gear store URL must be HTTPS.');
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('Gear store URL must be a clean HTTPS origin/path.');
  }
  await WebBrowser.openBrowserAsync(parsed.toString(), {
    createTask: false,
  });
}

export function isAllowedGearHost(
  url: string,
  allowedHost: string | null | undefined,
): boolean {
  if (!allowedHost) {
    return false;
  }
  try {
    const parsed = new URL(url);
    const allowed = new URL(
      allowedHost.startsWith('http') ? allowedHost : `https://${allowedHost}`,
    );
    return parsed.protocol === 'https:' && parsed.host === allowed.host;
  } catch {
    return false;
  }
}
