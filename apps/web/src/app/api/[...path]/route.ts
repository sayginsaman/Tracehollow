import { NextResponse, type NextRequest } from "next/server";

import { apiInternalUrl } from "@/lib/server-api";
import {
  bodyLimitFor,
  buildUpstreamPath,
  filterRequestHeaders,
  filterResponseHeaders,
  isAllowedHost,
  parseAllowedHosts,
  parseUploadLimit,
} from "@/lib/proxy";

export const dynamic = "force-dynamic";

const UPSTREAM_TIMEOUT_MS = 30_000;

function jsonError(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status, headers: { "cache-control": "no-store" } });
}

async function proxy(request: NextRequest, context: RouteContext<"/api/[...path]">) {
  const allowedHosts = parseAllowedHosts(process.env.TRACEHOLLOW_WEB_ALLOWED_HOSTS);
  if (!isAllowedHost(request.headers.get("host"), allowedHosts)) {
    return jsonError(421, "host_not_allowed");
  }

  const { path } = await context.params;
  const upstreamPath = buildUpstreamPath(path);
  if (upstreamPath === null) return jsonError(404, "not_found");

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const limit = bodyLimitFor(upstreamPath, parseUploadLimit(process.env.TRACEHOLLOW_WEB_MAX_UPLOAD_BYTES));
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (declared > limit) return jsonError(413, "request_body_too_large");
    body = await request.arrayBuffer();
    if (body.byteLength > limit) return jsonError(413, "request_body_too_large");
  }

  const url = `${apiInternalUrl()}${upstreamPath}${request.nextUrl.search}`;
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers: filterRequestHeaders(request.headers),
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return jsonError(502, "api_unreachable");
  }

  return new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: filterResponseHeaders(upstream.headers),
  });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
