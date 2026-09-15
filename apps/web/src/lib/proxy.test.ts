import { describe, expect, it } from "vitest";

import {
  MAX_PROXY_BODY_BYTES,
  bodyLimitFor,
  buildUpstreamPath,
  filterRequestHeaders,
  filterResponseHeaders,
  isAllowedHost,
  parseAllowedHosts,
} from "./proxy";

describe("buildUpstreamPath", () => {
  it("joins safe segments under /api", () => {
    expect(buildUpstreamPath(["v1", "auth", "login"])).toBe("/api/v1/auth/login");
    expect(buildUpstreamPath(["health", "ready"])).toBe("/api/health/ready");
  });

  it.each([[[]], [[".."]], [["v1", ".", "x"]], [["v1", "a/b"]], [["v1", "%2e%2e"]], [["v1", "a\\b"]]])(
    "rejects unsafe segments %j",
    (segments) => {
      expect(buildUpstreamPath(segments)).toBeNull();
    },
  );
});

describe("isAllowedHost", () => {
  const allowed = parseAllowedHosts(undefined);

  it("accepts loopback names on any port", () => {
    expect(isAllowedHost("localhost:3000", allowed)).toBe(true);
    expect(isAllowedHost("127.0.0.1:3100", allowed)).toBe(true);
    expect(isAllowedHost("[::1]:3000", allowed)).toBe(true);
  });

  it("rejects rebinding hostnames and missing hosts", () => {
    expect(isAllowedHost("attacker.example:3000", allowed)).toBe(false);
    expect(isAllowedHost("localhost.attacker.example", allowed)).toBe(false);
    expect(isAllowedHost(null, allowed)).toBe(false);
  });

  it("honours an explicit allowlist", () => {
    expect(isAllowedHost("tracehollow.lan", parseAllowedHosts("tracehollow.lan"))).toBe(true);
    expect(isAllowedHost("localhost", parseAllowedHosts("tracehollow.lan"))).toBe(false);
  });
});

describe("header filtering", () => {
  it("forwards only allowlisted request headers", () => {
    const incoming = new Headers({
      cookie: "tracehollow_session=abc",
      origin: "http://localhost:3000",
      "x-csrf-token": "token",
      "x-forwarded-for": "203.0.113.9",
      authorization: "Bearer leaked",
      host: "localhost:3000",
    });
    const outgoing = filterRequestHeaders(incoming);
    expect(outgoing.get("cookie")).toBe("tracehollow_session=abc");
    expect(outgoing.get("origin")).toBe("http://localhost:3000");
    expect(outgoing.get("x-csrf-token")).toBe("token");
    expect(outgoing.has("x-forwarded-for")).toBe(false);
    expect(outgoing.has("authorization")).toBe(false);
    expect(outgoing.has("host")).toBe(false);
  });

  it("keeps every Set-Cookie header and defaults to no-store", () => {
    const upstream = new Headers();
    upstream.append("set-cookie", "a=1; Path=/; HttpOnly");
    upstream.append("set-cookie", "b=2; Path=/; HttpOnly");
    upstream.set("content-type", "application/json");
    upstream.set("server", "uvicorn");
    const outgoing = filterResponseHeaders(upstream);
    expect(outgoing.getSetCookie()).toEqual(["a=1; Path=/; HttpOnly", "b=2; Path=/; HttpOnly"]);
    expect(outgoing.get("cache-control")).toBe("no-store");
    expect(outgoing.has("server")).toBe(false);
  });
});

describe("bodyLimitFor", () => {
  it("allows larger bodies only for evidence imports", () => {
    expect(bodyLimitFor("/api/v1/cases/abc/evidence/imports", 6_000_000)).toBe(6_000_000);
    expect(bodyLimitFor("/api/v1/cases/abc/entities", 6_000_000)).toBe(MAX_PROXY_BODY_BYTES);
    expect(bodyLimitFor("/api/v1/cases/abc/evidence/imports/extra", 6_000_000)).toBe(MAX_PROXY_BODY_BYTES);
  });

  it("forwards download headers needed for evidence", () => {
    const upstream = new Headers({
      "content-disposition": 'attachment; filename="evidence.txt"',
      "x-evidence-sha256": "a".repeat(64),
    });
    const outgoing = filterResponseHeaders(upstream);
    expect(outgoing.get("content-disposition")).toContain("attachment");
    expect(outgoing.get("x-evidence-sha256")).toBe("a".repeat(64));
  });
});
