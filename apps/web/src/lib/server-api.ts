import "server-only";

import { cookies } from "next/headers";

import type { Readiness, SessionInfo, SetupStatus } from "./api-types";

export const SESSION_COOKIE_NAME = "tracehollow_session";
const REQUEST_TIMEOUT_MS = 5000;

export type ServerResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "error"; status: number; data: unknown }
  | { kind: "unreachable" };

export function apiInternalUrl(): string {
  return (process.env.TRACEHOLLOW_API_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function getJson<T>(path: string, withSession: boolean): Promise<ServerResult<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (withSession) {
    const token = (await cookies()).get(SESSION_COOKIE_NAME)?.value;
    if (!token) return { kind: "error", status: 401, data: null };
    headers.Cookie = `${SESSION_COOKIE_NAME}=${token}`;
  }
  try {
    const response = await fetch(`${apiInternalUrl()}${path}`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const data: unknown = await response.json().catch(() => null);
    return response.ok ? { kind: "ok", data: data as T } : { kind: "error", status: response.status, data };
  } catch {
    return { kind: "unreachable" };
  }
}

export function fetchSetupStatus(): Promise<ServerResult<SetupStatus>> {
  return getJson<SetupStatus>("/api/v1/setup/status", false);
}

export function fetchSession(): Promise<ServerResult<SessionInfo>> {
  return getJson<SessionInfo>("/api/v1/auth/session", true);
}

/** Readiness returns 503 with a valid body when dependencies fail; keep that body. */
export async function fetchReadiness(): Promise<Readiness | null> {
  const result = await getJson<Readiness>("/api/health/ready", false);
  if (result.kind === "ok") return result.data;
  if (result.kind === "error" && result.status === 503 && isReadiness(result.data)) {
    return result.data;
  }
  return null;
}

function isReadiness(value: unknown): value is Readiness {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    "checks" in value &&
    typeof (value as { checks: unknown }).checks === "object"
  );
}
