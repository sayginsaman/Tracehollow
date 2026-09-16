import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TEST_CASE, mockApi, renderInCase } from "@/test/workspace";
import type { AiMessage, AiRun, AiStatus, CaseAi, CitationDetail, ConversationDetail } from "@/lib/workspace-types";

import { AiWorkspace } from "./AiWorkspace";
import { AnswerView } from "./AnswerView";
import { CitationPanel } from "./CitationPanel";
import { AI_POLL_INTERVAL_MS, ConversationView } from "./ConversationView";

const API = `/api/v1/cases/${TEST_CASE.id}`;

const STATUS: AiStatus = {
  enabled: true,
  local_provider: "ollama",
  local_location: "local",
  local_generation_model: "qwen3:8b",
  local_embedding_model: "qwen3-embedding:0.6b",
  synthetic: false,
  cloud_provider: "anthropic",
  cloud_model: "claude-sonnet-5",
  cloud_configured: true,
  checks: [],
};

const CASE_AI: CaseAi = {
  enabled: true,
  mode: "local_only",
  policy_version: 1,
  local_location: "local",
  cloud_available: true,
  index: { pending: 0, indexing: 0, indexed: 3, stale: 1, failed: 1, canceled: 0, total: 5 },
  embedding_profile: { provider: "ollama", model: "qwen3-embedding:0.6b", dimensions: 1024, chunking_version: 1, indexing_version: 1, activated_at: null, synthetic: false },
  active_runs: 0,
};

function run(overrides: Partial<AiRun> = {}): AiRun {
  return {
    id: "run1",
    case_id: TEST_CASE.id,
    conversation_id: "conv1",
    run_type: "answer",
    status: "completed",
    stage: "done",
    question: "Kim tescil etti?",
    requested_location: "local",
    provider: "ollama",
    model: "qwen3:8b",
    processing_location: "local",
    prompt_template_version: "answer-v1+plan-v1",
    usage: { input_tokens: 2650, output_tokens: 378, source: "provider_reported", cost: "unknown" },
    coverage: { notes: [] },
    validation: { claims_removed: [{ kind: "fact", reason: "no_verified_citation" }] },
    tool_calls: [{ ref: "T1", tool: "count_evidence", arguments: {}, result: { count: 10 } }, { tool: "start_collection", rejected: "unregistered_tool" }],
    retrieval: { chunks: [{ chunk_id: "c1", evidence_id: "ev1" }], retrievers: { semantic: { used: true } } },
    error_code: null,
    error_detail: null,
    queued_at: "2026-09-16T10:00:00Z",
    started_at: "2026-09-16T10:00:01Z",
    finished_at: "2026-09-16T10:00:30Z",
    cancel_requested_at: null,
    synthetic: false,
    ...overrides,
  };
}

const hostile = '<img src=x onerror="window.__aiPwned=true"><script>window.__aiPwned=true</script>';

const answer: AiMessage = {
  id: "m2",
  role: "assistant",
  kind: "answer",
  content: "",
  ai_run_id: "run1",
  created_at: "2026-09-16T10:00:30Z",
  answer: {
    status: "partially_answered",
    claims: [
      { text: `Örnek A.Ş. registered ornek.example ${hostile}`, kind: "fact", citations: [{ citation_id: "cit1", label: "E1", ref_type: "chunk" }] },
      { text: "Reports disagree about the address.", kind: "conflict", citations: [{ citation_id: "cit2", label: "E2", ref_type: "chunk" }, { citation_id: "cit3", label: "E3", ref_type: "chunk" }] },
      { text: "There are 10 evidence records.", kind: "count", citations: [{ citation_id: "cit4", label: "T1", ref_type: "tool" }] },
      { text: "The registrant's lawyer is not named.", kind: "insufficient", citations: [] },
    ],
    limitations: ["Only two resolution reports exist."],
    coverage_notes: ["1 connector run(s) in this case were partial, failed or canceled; missing information may exist outside the collected evidence."],
    server_notes: ["1 statement(s) were removed because they could not be verified."],
    synthetic_model: false,
  },
  citations: [
    { id: "cit1", label: "E1", ref_type: "chunk", evidence_id: "ev1", evidence_title: "Registry extract", tool_name: null, source_available: true },
    { id: "cit4", label: "T1", ref_type: "tool", evidence_id: null, evidence_title: null, tool_name: "count_evidence", source_available: true },
  ],
};

describe("AnswerView", () => {
  it("labels claim kinds, renders model text inertly and opens citations", async () => {
    const onOpen = vi.fn();
    const { container } = renderInCase(<AnswerView message={answer} run={run()} onOpenCitation={onOpen} selectedCitation={null} />);

    expect(screen.getByText(/Partially answered/)).toBeInTheDocument();
    expect(screen.getByText("Sourced")).toBeInTheDocument();
    expect(screen.getByText("Conflict")).toBeInTheDocument();
    expect(screen.getByText("Database count")).toBeInTheDocument();
    expect(screen.getByText("Insufficient evidence")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/onerror=/)).toBeInTheDocument();
    expect((window as unknown as { __aiPwned?: boolean }).__aiPwned).toBeUndefined();
    expect(screen.getByText(/missing information may exist outside/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open citation E1: Evidence: Registry extract" }));
    expect(onOpen).toHaveBeenCalledWith("cit1");

    await userEvent.click(screen.getByText("How this was produced"));
    expect(screen.getByText("ollama · qwen3:8b")).toBeInTheDocument();
    expect(screen.getByText(/2650 in \/ 378 out \(reported by the provider\)/)).toBeInTheDocument();
    expect(screen.getByText(/Unknown \(Tracehollow does not estimate provider prices\)/)).toBeInTheDocument();
    expect(screen.getByText(/Ignored 1 tool request\(s\) from the model: start_collection/)).toBeInTheDocument();
  });

  it("tells a disagreement apart from a change, and marks statements about another subject", () => {
    renderInCase(
      <AnswerView
        message={{
          ...answer,
          answer: {
            ...answer.answer!,
            claims: [
              { text: "Inventory and email give different hosts.", kind: "conflict", difference_type: "disagreement", citations: [] },
              { text: "The address changed on 2026-09-01.", kind: "conflict", difference_type: "change_over_time", citations: [] },
              { text: "Two undated records differ.", kind: "conflict", difference_type: "undetermined", citations: [] },
              { text: "shop.example.test uses Test Authority.", kind: "fact", answers_question: false, applicability: "other_subject", citations: [] },
            ],
          },
        }}
        run={undefined}
        onOpenCitation={() => undefined}
        selectedCitation={null}
      />,
    );
    expect(screen.getByText("Sources disagree")).toBeInTheDocument();
    expect(screen.getByText("Change over time")).toBeInTheDocument();
    expect(screen.getByText("Difference, cause unknown")).toBeInTheDocument();
    expect(screen.getByText("Other subject")).toBeInTheDocument();
  });

  it("marks synthetic fixture answers", () => {
    renderInCase(
      <AnswerView
        message={{ ...answer, answer: { ...answer.answer!, synthetic_model: true, status: "insufficient_evidence" } }}
        run={undefined}
        onOpenCitation={() => undefined}
        selectedCitation={null}
      />,
    );
    expect(screen.getByText("Synthetic model")).toBeInTheDocument();
    expect(screen.getByText(/Insufficient evidence: the case material does not support an answer/)).toBeInTheDocument();
  });
});

describe("CitationPanel", () => {
  it("highlights the exact passage re-read from the original evidence", async () => {
    const detail: CitationDetail = {
      id: "cit1",
      label: "E1",
      ref_type: "chunk",
      claim_index: 0,
      ai_run_id: "run1",
      tool_name: null,
      tool_result: null,
      passage: {
        evidence_id: "ev1",
        evidence_title: "Registry extract",
        acquisition_method: "authorized_import",
        synthetic: false,
        collected_at: "2026-09-03T08:00:00Z",
        source_published_at: "2026-09-02T06:00:00Z",
        source_published_at_original: "2026-09-02T09:00:00+03:00",
        source_reference: null,
        kind: "text",
        status: "available",
        integrity: "verified",
        before: "Kayıt özeti: ornek.example alan adı, İstanbul merkezli ",
        passage: "Örnek A.Ş. tarafından",
        after: " 2026-09-01 tarihinde tescil edildi.",
        char_start: 55,
        char_end: 76,
        json_pointer: null,
        json_value: null,
        chunk_text: null,
        quote: "Örnek A.Ş. tarafından",
      },
    };
    mockApi({ [`${API}/ai/citations/cit1`]: detail });
    renderInCase(<CitationPanel citationId="cit1" onClose={() => undefined} />);

    const panel = await screen.findByRole("complementary", { name: "Citation details" });
    const passage = await within(panel).findByLabelText("Cited passage in context");
    expect(within(passage).getByText("Örnek A.Ş. tarafından").tagName).toBe("MARK");
    expect(passage.textContent).toContain("İstanbul merkezli Örnek A.Ş. tarafından 2026-09-01");
    expect(within(panel).getByText("2026-09-02T09:00:00+03:00")).toBeInTheDocument();
    expect(within(panel).getByText(/characters 55–76 of the original/)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Registry extract" })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/evidence/ev1`);
  });

  it("says when the cited source was deleted instead of showing content", async () => {
    mockApi({
      [`${API}/ai/citations/cit9`]: {
        id: "cit9", label: "E1", ref_type: "chunk", claim_index: 0, ai_run_id: "run1", tool_name: null, tool_result: null,
        passage: {
          evidence_id: null, evidence_title: null, acquisition_method: null, synthetic: false, collected_at: null,
          source_published_at: null, source_published_at_original: null, source_reference: null, kind: null,
          status: "source_deleted", integrity: null, before: null, passage: null, after: null, char_start: null,
          char_end: null, json_pointer: null, json_value: null, chunk_text: null, quote: null,
        },
      },
    });
    renderInCase(<CitationPanel citationId="cit9" onClose={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/was deleted after this answer was generated/);
    expect(screen.queryByLabelText("Cited passage in context")).toBeNull();
  });
});

describe("ConversationView", () => {
  it("polls a running question and explains a model failure without inventing an answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const question: AiMessage = { id: "m1", role: "user", kind: "question", content: "Kim tescil etti?", answer: null, ai_run_id: "run1", created_at: "2026-09-16T10:00:00Z", citations: [] };
    const running: ConversationDetail = {
      conversation: { id: "conv1", case_id: TEST_CASE.id, title: "Kim tescil etti?", created_at: "2026-09-16T10:00:00Z", updated_at: "2026-09-16T10:00:00Z", message_count: 1 },
      messages: [question],
      runs: [run({ status: "running", stage: "generating", finished_at: null })],
    };
    const failed: ConversationDetail = { ...running, runs: [run({ status: "failed", stage: "generating", error_code: "model_unavailable", error_detail: "Ollama could not be reached." })] };
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
      if (url.startsWith(`${API}/ai/conversations/conv1`)) {
        calls += 1;
        return json(calls === 1 ? running : failed);
      }
      if (url.startsWith("/api/v1/ai/status")) return json(STATUS);
      if (url.startsWith(`${API}/ai`)) return json(CASE_AI);
      return new Response("{}", { status: 404 });
    });
    try {
      renderInCase(<ConversationView conversationId="conv1" />);
      expect(await screen.findByText(/Generating the answer with the model/)).toBeInTheDocument();
      expect(screen.getByText(/Local models can take a minute or more/)).toBeInTheDocument();
      expect(screen.getByRole("status", { name: "AI processing location" })).toHaveTextContent(/Local processing only/);
      // A local-only case offers no cloud choice.
      expect(screen.queryByText(/With the cloud provider/)).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(AI_POLL_INTERVAL_MS);
      });
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Start Ollama on the host/));
      expect(screen.queryByText("Sourced")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("AiWorkspace", () => {
  it("requires an explicit acknowledgement before allowing cloud processing", async () => {
    const fetchMock = mockApi({
      "/api/v1/ai/status": STATUS,
      [`${API}/ai/index`]: { items: [], total: 0, limit: 10, offset: 0 },
      [`${API}/ai/conversations`]: { items: [], total: 0, limit: 20, offset: 0 },
      [`${API}/ai/outputs`]: { items: [], total: 0, limit: 5, offset: 0 },
      [`${API}/ai/runs`]: { items: [], total: 0, limit: 5, offset: 0 },
      [`${API}/ai`]: CASE_AI,
    });
    renderInCase(<AiWorkspace />);
    await userEvent.click(await screen.findByLabelText(/Cloud allowed/));
    const save = screen.getByRole("button", { name: "Save AI setting" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(save).toBeEnabled();
    expect(screen.getByText(/Stale: 1/)).toBeInTheDocument();
    expect(screen.getByText(/may be missing from search until rebuilt/)).toBeInTheDocument();
    await userEvent.click(save);
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(patch?.[0]).toBe(`${API}/ai/settings`);
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ mode: "cloud_allowed", acknowledge_cloud_processing: true });
  });

  it("shows only a clear notice when AI is disabled for the installation", async () => {
    mockApi({ "/api/v1/ai/status": { ...STATUS, enabled: false }, [`${API}/ai`]: { ...CASE_AI, enabled: false } });
    renderInCase(<AiWorkspace />);
    expect(await screen.findByText(/AI features are disabled for this installation/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();
    expect(screen.queryByText("Evidence index")).toBeNull();
  });
});
