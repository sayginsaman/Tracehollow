import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import { CaseProvider } from "@/components/cases/CaseContext";
import type { SessionInfo } from "@/lib/api-types";
import { SessionProvider } from "@/lib/session-context";
import type { CaseDetail } from "@/lib/workspace-types";

export const TEST_CASE: CaseDetail = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "Synthetic case",
  purpose: "",
  scope: "",
  tags: [],
  status: "active",
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
  archived_at: null,
  counts: { entities: 0, relationships: 0, evidence: 0, notes: 0, saved_queries: 0, query_runs: 0, active_runs: 0 },
  my_role: "analyst",
  permissions: [
    "ai.request",
    "audit.case.read",
    "case.delete",
    "case.edit",
    "case.members.manage",
    "case.read",
    "evidence.import",
    "exports.create",
    "monitors.manage",
    "queries.run",
    "retention.manage",
  ],
};

/** The same case as a viewer member sees it. */
export const VIEWER_CASE: CaseDetail = { ...TEST_CASE, my_role: "viewer", permissions: ["case.read"] };

export const SESSION: SessionInfo = {
  user: { id: "u1", username: "analyst", role: "administrator", is_admin: true },
  permissions: [
    "accounts.manage",
    "accounts.search",
    "audit.system.read",
    "case_access.manage",
    "cases.create",
    "credentials.manage",
    "notification_destinations.manage",
  ],
  csrf_token: "csrf-token",
  expires_at: "2026-09-16T10:00:00Z",
  idle_expires_at: "2026-09-15T18:00:00Z",
};

/** Routes fetch calls to JSON fixtures by URL prefix (longest prefix wins). */
export function mockApi(routes: Record<string, unknown>) {
  const entries = Object.entries(routes).sort(([a], [b]) => b.length - a.length);
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const match = entries.find(([prefix]) => url.startsWith(prefix));
    if (!match) return new Response(JSON.stringify({ detail: "not_found" }), { status: 404 });
    return new Response(JSON.stringify(match[1]), { status: 200, headers: { "content-type": "application/json" } });
  });
}

export const VIEWER_SESSION: SessionInfo = {
  ...SESSION,
  user: { id: "u2", username: "reader", role: "viewer", is_admin: false },
  permissions: [],
};

export function renderWithSession(ui: ReactElement, session: SessionInfo = SESSION) {
  return render(<SessionProvider session={session}>{ui}</SessionProvider>);
}

export function renderInCase(ui: ReactElement, caseDetail: CaseDetail = TEST_CASE, session: SessionInfo = SESSION) {
  return render(
    <SessionProvider session={session}>
      <CaseProvider initialCase={caseDetail}>{ui}</CaseProvider>
    </SessionProvider>,
  );
}
