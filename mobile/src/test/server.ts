/**
 * Local HTTP fixture helpers for transport tests (not MSW).
 */

export type FixtureRequest = {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
};

export type FixtureHandler = (
  request: FixtureRequest,
) => Promise<{
  status: number;
  headers?: Record<string, string>;
  body?: unknown;
}>;

export function createFixtureFetch(handler: FixtureHandler): typeof fetch {
  const fetchImpl: typeof fetch = async (input, init) => {
    const url =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    const method = (init?.method ?? 'GET').toUpperCase();
    const headers: Record<string, string> = {};
    const rawHeaders = init?.headers;
    if (rawHeaders instanceof Headers) {
      rawHeaders.forEach((value, key) => {
        headers[key] = value;
      });
    } else if (Array.isArray(rawHeaders)) {
      for (const [key, value] of rawHeaders) {
        headers[key] = value;
      }
    } else if (rawHeaders) {
      for (const [key, value] of Object.entries(rawHeaders)) {
        headers[key] = String(value);
      }
    }

    let body: string | null = null;
    if (typeof init?.body === 'string') {
      body = init.body;
    }

    const result = await handler({ url, method, headers, body });
    const responseHeaders = new Headers({
      ...(result.status === 204 ? {} : { 'Content-Type': 'application/json' }),
      ...(result.headers ?? {}),
    });
    // A 204 response must not carry a body — `new Response('', { status: 204 })` throws.
    const payload =
      result.status === 204
        ? null
        : result.body === undefined
          ? '{}'
          : JSON.stringify(result.body);
    return new Response(payload, {
      status: result.status,
      headers: responseHeaders,
    });  };
  return fetchImpl;
}

export function headerMapLower(
  headers: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    out[key.toLowerCase()] = value;
  }
  return out;
}
