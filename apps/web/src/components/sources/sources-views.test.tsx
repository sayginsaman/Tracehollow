import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { QueriesView } from "@/components/cases/QueriesView";
import { changedParameters, describeProgress, formatQuota, observationLabel } from "@/lib/connectors";
import { SessionProvider } from "@/lib/session-context";
import type { ConnectorDescriptor, Observation } from "@/lib/workspace-types";
import { TEST_CASE, mockApi, renderInCase } from "@/test/workspace";

import { SourcesView } from "./SourcesView";

const HEALTH = { last_run_at: null, last_outcome: null, last_error_code: null, last_quota: null, recent_outcomes: {} };

const GITHUB: ConnectorDescriptor = {
  connector_id: "github.account",
  version: "1.0.0",
  display_name: "GitHub account",
  synthetic: false,
  description: "Looks up a GitHub account.",
  supported_input_types: ["username"],
  collection_mode: "third_party_api",
  credential_requirements: "Optional personal access token.",
  coverage: "Public profile fields.",
  max_pages: 10,
  max_items_per_page: 100,
  timeout_seconds: 120,
  retry_max_attempts: 3,
  retryable_outcomes: ["unavailable", "rate_limited"],
  output_schema: "tracehollow.github.account/v1",
  cost_model: "Free API; subject to GitHub rate limits.",
  quota_notes: "60 requests/hour without a token.",
  cache_policy: "No caching.",
  max_concurrent_runs: 2,
  min_request_interval_seconds: 1,
  provider_terms: "GitHub terms.",
  documentation: "docs/connectors/github.md",
  last_live_verification: null,
  verification_status: "fixture_tested",
  parameters: [
    { name: "include_repositories", kind: "boolean", label: "Include repositories", description: "Also list repositories.", default: true, choices: null, minimum: null, maximum: null },
  ],
  credentials: [
    { name: "token", label: "Personal access token", description: "Public data needs no permissions.", required: false, configured: true, usable: true, updated_at: "2026-09-15T10:00:00Z", last_used_at: null, last_result: null },
  ],
  health: { ...HEALTH, last_run_at: "2026-09-15T11:00:00Z", last_outcome: "rate_limited", last_quota: { limit: 60, remaining: 0, authenticated: false, cost: "none" }, recent_outcomes: { rate_limited: 1 } },
};

const SHERLOCK: ConnectorDescriptor = {
  ...GITHUB,
  connector_id: "username.sherlock",
  display_name: "Username discovery (Sherlock)",
  collection_mode: "platform_probe",
  credential_requirements: "none",
  max_pages: 1,
  parameters: [
    { name: "sites", kind: "multi_choice", label: "Platforms", description: "Platforms to check.", default: ["GitHub", "GitLab"], choices: { GitHub: "https://github.com/", GitLab: "https://gitlab.com/", Codeberg: "https://codeberg.org/" }, minimum: null, maximum: 3 },
    { name: "timeout_seconds", kind: "integer", label: "Per-platform timeout", description: "Seconds.", default: 15, choices: null, minimum: 5, maximum: 60 },
  ],
  credentials: [],
  health: HEALTH,
};

const SESSION = (admin: boolean) => ({
  user: { id: "u1", username: "analyst", is_admin: admin },
  csrf_token: "csrf-token",
  expires_at: "2026-09-16T10:00:00Z",
  idle_expires_at: "2026-09-15T18:00:00Z",
});

describe("SourcesView", () => {
  it("shows mode, verification, quota and health without ever showing a credential value", async () => {
    const fetchMock = mockApi({ "/api/v1/connectors": [GITHUB] });
    render(
      <SessionProvider session={SESSION(true)}>
        <SourcesView />
      </SessionProvider>,
    );
    expect(await screen.findByText("GitHub account")).toBeInTheDocument();
    expect(screen.getByText(/Third-party lookup\./)).toBeInTheDocument();
    expect(screen.getByText("Fixture-tested: not live-verified")).toBeInTheDocument();
    expect(screen.getByText(/0 of 60 requests left \(without token\)/)).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Replace credential" }));
    const input = screen.getByLabelText("New value for Personal access token");
    expect(input).toHaveAttribute("type", "password");
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...GITHUB }), { status: 200, headers: { "content-type": "application/json" } }));
    await user.type(input, "ghp_secretvalue");
    await user.click(screen.getByRole("button", { name: "Save credential" }));
    await waitFor(() => expect(screen.queryByLabelText("New value for Personal access token")).not.toBeInTheDocument());
    const [, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ value: "ghp_secretvalue" });
    expect(document.body.textContent).not.toContain("ghp_secretvalue");
  });

  it("does not offer credential changes to non-administrators", async () => {
    mockApi({ "/api/v1/connectors": [GITHUB] });
    render(
      <SessionProvider session={SESSION(false)}>
        <SourcesView />
      </SessionProvider>,
    );
    expect(await screen.findByText("Only an administrator can change credentials.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /credential/ })).not.toBeInTheDocument();
  });
});

describe("QueriesView", () => {
  it("renders connector parameters and saves only values that differ from the defaults", async () => {
    const fetchMock = mockApi({
      "/api/v1/connectors": [SHERLOCK, GITHUB],
      [`/api/v1/cases/${TEST_CASE.id}/saved-queries`]: { items: [], total: 0, limit: 100, offset: 0 },
      [`/api/v1/cases/${TEST_CASE.id}/runs`]: { items: [], total: 0, limit: 20, offset: 0 },
    });
    renderInCase(<QueriesView />);
    const user = userEvent.setup();
    await screen.findByRole("group", { name: "Platforms" });
    expect(screen.getByText(/Platform probe:/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Maximum pages")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Codeberg"));
    await user.click(screen.getByLabelText("GitLab"));
    await user.type(screen.getByLabelText("Name"), "Handle check");
    await user.type(screen.getByLabelText("Input value"), "ornekdev");
    await user.click(screen.getByRole("button", { name: "Save query" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
      expect(post).toBeTruthy();
    });
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST") as [string, RequestInit];
    expect(JSON.parse(String(post[1].body))).toEqual({
      name: "Handle check",
      input_type: "username",
      input_value: "ornekdev",
      connector_ids: ["username.sherlock"],
      parameters: { sites: ["GitHub", "Codeberg"] },
      limits: { max_pages: 1, max_items_per_page: 100 },
    });

    await user.selectOptions(screen.getByLabelText("Source"), "github.account");
    expect(screen.getByText(/Third-party lookup:/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Include repositories/)).toBeChecked();
  });
});

describe("connector presentation helpers", () => {
  it("labels observations from every connector and formats quota and progress", () => {
    const base: Omit<Observation, "observation_type" | "payload"> = {
      id: "o1",
      entity_id: null,
      evidence_id: "e1",
      query_run_id: "r1",
      connector_run_id: "c1",
      source_object_id: "x",
      collected_at: "2026-09-15T10:00:00Z",
      event_time: null,
      source_published_at: null,
      created_at: "2026-09-15T10:00:00Z",
    };
    expect(observationLabel({ ...base, observation_type: "candidate_account", payload: { username: "ornekdev", platform: "GitHub" } })).toEqual({ primary: "ornekdev", secondary: "candidate on GitHub" });
    expect(observationLabel({ ...base, observation_type: "subdomain", payload: { host: "mail.ornek.example", sources: ["crtsh"] } }).primary).toBe("mail.ornek.example");
    expect(observationLabel({ ...base, observation_type: "web_page", payload: { title: "Örnek", http_status: 200 } }).secondary).toBe("HTTP 200");
    expect(formatQuota(null)).toBe("Not reported by this source");
    expect(describeProgress({ progress: { waiting_for_slot: true, limit: 1 } })).toMatch(/Waiting for a free slot/);
    expect(describeProgress({ progress: { sites_checked: 3, sites_selected: 15 } })).toBe("3 of 15 platform(s) checked.");
    expect(changedParameters(SHERLOCK.parameters, { sites: ["GitHub", "GitLab"], timeout_seconds: 20 })).toEqual({ timeout_seconds: 20 });
  });
});

vi.mock("next/navigation", async () => {
  const actual = await vi.importActual<typeof import("next/navigation")>("next/navigation");
  return { ...actual, useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }), usePathname: () => "/sources" };
});
