/**
 * Pure helpers for the same-origin API proxy (src/app/api/[...path]/route.ts).
 *
 * The browser only talks to the web origin. The proxy forwards a fixed allowlist of
 * headers to the internal API, so cookies stay first-party and no CORS is required.
 */

export const MAX_PROXY_BODY_BYTES = 1024 * 1024;

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "cookie",
  "origin",
  "sec-fetch-site",
  "x-csrf-token",
  "x-request-id",
] as const;

const FORWARDED_RESPONSE_HEADERS = [
  "cache-control",
  "content-type",
  "retry-after",
  "x-request-id",
] as const;

const SEGMENT_PATTERN = /^[A-Za-z0-9._~-]+$/;

/** Returns the upstream path for validated segments, or null if any segment is unsafe. */
export function buildUpstreamPath(segments: readonly string[]): string | null {
  if (segments.length === 0) return null;
  for (const segment of segments) {
    if (segment === "." || segment === ".." || !SEGMENT_PATTERN.test(segment)) {
      return null;
    }
  }
  return `/api/${segments.join("/")}`;
}

/** Parses a comma-separated host allowlist such as "localhost,127.0.0.1,[::1]". */
export function parseAllowedHosts(value: string | undefined): string[] {
  const source = value ?? "localhost,127.0.0.1,[::1]";
  return source
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);
}

/** DNS-rebinding protection: accept only configured Host names (any port). */
export function isAllowedHost(hostHeader: string | null, allowedHosts: readonly string[]): boolean {
  if (!hostHeader) return false;
  const host = hostHeader.trim().toLowerCase();
  const hostname = host.startsWith("[")
    ? host.slice(0, host.indexOf("]") + 1)
    : host.split(":")[0] ?? "";
  return hostname !== "" && allowedHosts.includes(hostname);
}

export function filterRequestHeaders(incoming: Headers): Headers {
  const outgoing = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value !== null) outgoing.set(name, value);
  }
  return outgoing;
}

export function filterResponseHeaders(upstream: Headers): Headers {
  const outgoing = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.get(name);
    if (value !== null) outgoing.set(name, value);
  }
  for (const cookie of upstream.getSetCookie()) {
    outgoing.append("set-cookie", cookie);
  }
  if (!outgoing.has("cache-control")) outgoing.set("cache-control", "no-store");
  return outgoing;
}
