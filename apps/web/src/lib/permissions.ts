import type { SessionInfo } from "./api-types";
import type { CaseDetail } from "./workspace-types";

/** System permissions (account role). Mirrors app/auth/permissions.py on the API. */
export type SystemPermission =
  | "cases.create"
  | "accounts.search"
  | "accounts.manage"
  | "case_access.manage"
  | "credentials.manage"
  | "notification_destinations.manage"
  | "audit.system.read";

/** Case permissions (effective case role). */
export type CasePermission =
  | "case.read"
  | "case.edit"
  | "case.members.manage"
  | "evidence.import"
  | "queries.run"
  | "monitors.manage"
  | "ai.request"
  | "exports.create"
  | "retention.manage"
  | "case.delete"
  | "audit.case.read";

export function hasSystemPermission(session: SessionInfo, permission: SystemPermission): boolean {
  return (session.permissions ?? []).includes(permission);
}

export function caseCan(caseDetail: Pick<CaseDetail, "permissions">, permission: CasePermission): boolean {
  return (caseDetail.permissions ?? []).includes(permission);
}

export const ROLE_LABELS: Record<string, string> = {
  administrator: "Administrator",
  analyst: "Analyst",
  viewer: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<string, string> = {
  administrator: "Manages accounts, case access, source credentials and notification destinations. Opens a case only as its member.",
  analyst: "Works on the cases they belong to: collects, imports, asks AI, exports and manages members.",
  viewer: "Reads the cases they belong to, including single evidence files. Cannot change, collect, request AI or export.",
};

/** Why an action is unavailable for a read-only role, in plain words. */
export const VIEWER_NOTE = "Your role in this case is viewer: you can read everything but not change, collect, request AI or export.";
