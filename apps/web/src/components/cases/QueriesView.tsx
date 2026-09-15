"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ConnectorDescriptor, Page, QueryRun, QueryRunDetail, SavedQuery } from "@/lib/workspace-types";

import {
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  LoadingState,
  Notice,
  Pagination,
  RunStatusBadge,
  Section,
  Select,
  SyntheticBadge,
  TextInput,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";

const RUN_PAGE_SIZE = 20;

export function QueriesView() {
  const { apiBase, base, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  const queries = useResource<Page<SavedQuery>>(`${apiBase}/saved-queries?limit=100`);
  const [runOffset, setRunOffset] = useState(0);
  const runs = useResource<Page<QueryRun>>(`${apiBase}/runs?limit=${RUN_PAGE_SIZE}&offset=${runOffset}`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const fixture = connectors.data?.find((connector) => connector.connector_id === "synthetic.fixture");
  const scenarios = fixture?.parameters.scenario ?? {};

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("create");
    setError(null);
    try {
      await mutate(`${apiBase}/saved-queries`, {
        body: {
          name: String(form.get("name") ?? ""),
          input_type: String(form.get("input_type")),
          input_value: String(form.get("input_value") ?? ""),
          connector_ids: ["synthetic.fixture"],
          parameters: { scenario: String(form.get("scenario")) },
          limits: { max_pages: Number(form.get("max_pages")), max_items_per_page: 5 },
        },
      });
      formElement.reset();
      await Promise.all([queries.reload(), refreshCase()]);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function runQuery(queryId: string) {
    setBusy(queryId);
    setError(null);
    try {
      const run = await mutate<QueryRunDetail>(`${apiBase}/saved-queries/${queryId}/runs`);
      router.push(`${base}/runs/${run.id}`);
    } catch (caught) {
      setError(describeError(caught));
      setBusy(null);
    }
  }

  async function deleteQuery(queryId: string) {
    setBusy(queryId);
    setError(null);
    try {
      await mutate(`${apiBase}/saved-queries/${queryId}`, { method: "DELETE" });
      await Promise.all([queries.reload(), refreshCase()]);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <Section title="Available connectors" description="What each source can do, what it needs and what it costs, before you run anything.">
        {connectors.state === "error" ? <ErrorNotice error={connectors.error} /> : null}
        {connectors.data?.map((connector) => (
          <div key={connector.connector_id} className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{connector.display_name}</span>
              {connector.synthetic ? <SyntheticBadge /> : null}
              <span className="font-mono text-xs text-muted">
                {connector.connector_id} {connector.version}
              </span>
            </div>
            <p className="text-muted">{connector.description}</p>
            <ul className="grid gap-1 text-xs text-muted md:grid-cols-2">
              <li>Inputs: {connector.supported_input_types.join(", ")}</li>
              <li>Credentials: {connector.credential_requirements}</li>
              <li>Limits: up to {connector.max_pages} pages × {connector.max_items_per_page} items</li>
              <li>Retries: {connector.retry_max_attempts} attempts for {connector.retryable_outcomes.join(", ")}</li>
              <li>Cost: {connector.cost_model ?? "none (no external requests)"}</li>
              <li>Live verification: {connector.last_live_verification ?? "not applicable (synthetic)"}</li>
            </ul>
          </div>
        ))}
        <p className="mt-3 text-xs text-muted">
          No live OSINT connectors are installed in this version. Nothing here contacts a real target.
        </p>
      </Section>

      {writable ? (
        <Section title="New saved query" description="A saved query is a reusable definition. Each run stores its own snapshot of these parameters.">
          <form onSubmit={create} className="space-y-3">
            {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
            <div className="grid gap-3 md:grid-cols-4">
              <Field label="Name" htmlFor="query-name">
                <TextInput id="query-name" name="name" required maxLength={200} />
              </Field>
              <Field label="Input type" htmlFor="query-input-type">
                <Select id="query-input-type" name="input_type">
                  {(fixture?.supported_input_types ?? ["username"]).map((type) => (
                    <option key={type} value={type}>
                      {humanize(type)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Input value" htmlFor="query-input-value" hint="Used only to generate synthetic data.">
                <TextInput id="query-input-value" name="input_value" required maxLength={1000} />
              </Field>
              <Field label="Maximum pages" htmlFor="query-max-pages">
                <TextInput id="query-max-pages" name="max_pages" type="number" min={1} max={10} defaultValue={3} />
              </Field>
            </div>
            <Field label="Fixture scenario" htmlFor="query-scenario" hint={scenarios.findings}>
              <Select id="query-scenario" name="scenario" defaultValue="findings">
                {Object.entries(scenarios).map(([scenario, description]) => (
                  <option key={scenario} value={scenario}>
                    {humanize(scenario)} — {description}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" variant="primary" disabled={busy === "create"} aria-busy={busy === "create"}>
              Save query
            </Button>
          </form>
        </Section>
      ) : null}

      <Section title="Saved queries">
        {queries.state === "error" ? <ErrorNotice error={queries.error} onRetry={() => void queries.reload()} /> : null}
        {queries.state === "loading" && !queries.data ? <LoadingState /> : null}
        {queries.data && queries.data.items.length === 0 ? <EmptyState>No saved queries yet.</EmptyState> : null}
        {!writable && error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
        <ul className="divide-y divide-line text-sm">
          {queries.data?.items.map((query) => (
            <li key={query.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{query.name}</span>
                  {query.synthetic ? <SyntheticBadge /> : null}
                </div>
                <div className="text-xs text-muted">
                  {humanize(query.input_type)}: <span className="font-mono">{query.input_value}</span> · scenario{" "}
                  {String(query.parameters.scenario ?? "findings")} · up to {query.limits.max_pages ?? "—"} page(s) ·{" "}
                  {query.run_counter} run(s)
                </div>
              </div>
              <div className="flex items-center gap-2">
                {query.last_run_id && query.last_run_status ? (
                  <Link href={`${base}/runs/${query.last_run_id}`} className="flex items-center gap-1 text-xs text-accent hover:underline">
                    Last run <RunStatusBadge status={query.last_run_status} />
                  </Link>
                ) : null}
                {writable ? (
                  <>
                    <Button variant="primary" onClick={() => void runQuery(query.id)} disabled={busy === query.id}>
                      Run
                    </Button>
                    <Button onClick={() => void deleteQuery(query.id)} disabled={busy === query.id}>
                      Delete
                    </Button>
                  </>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
        {writable ? (
          <Notice>Deleting a saved query keeps its previous runs, snapshots and evidence.</Notice>
        ) : null}
      </Section>

      <Section title="Execution history" actions={<Button onClick={() => void runs.reload()}>Refresh</Button>}>
        {runs.state === "error" ? <ErrorNotice error={runs.error} onRetry={() => void runs.reload()} /> : null}
        {runs.state === "loading" && !runs.data ? <LoadingState /> : null}
        {runs.data && runs.data.items.length === 0 ? <EmptyState>No executions yet.</EmptyState> : null}
        {runs.data && runs.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Execution history</caption>
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Run</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Queued</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Evidence</th>
                  <th scope="col" className="py-2 font-medium">Observations</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id} className="border-t border-line">
                    <td className="py-2 pr-4">
                      <Link href={`${base}/runs/${run.id}`} className="text-accent hover:underline">
                        {run.saved_query_name ?? run.parameters_snapshot.saved_query_name ?? "Query"} #{run.run_number}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <RunStatusBadge status={run.status} />
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs">{formatUtc(run.queued_at)}</td>
                    <td className="py-2 pr-4">{run.evidence_count}</td>
                    <td className="py-2">{run.observation_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={runs.data.total} limit={RUN_PAGE_SIZE} offset={runOffset} onChange={setRunOffset} />
          </div>
        ) : null}
      </Section>
    </div>
  );
}
