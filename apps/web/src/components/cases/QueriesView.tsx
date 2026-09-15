"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { changedParameters, collectionMode, defaultParameters, VERIFICATION_TEXT } from "@/lib/connectors";
import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ConnectorDescriptor, Page, ParameterSpec, QueryRun, QueryRunDetail, SavedQuery } from "@/lib/workspace-types";

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

const INPUT_HINTS: Record<string, string> = {
  url: "An absolute http(s) address, for example https://example.org/about",
  username: "A username or account name",
  domain: "A domain name such as example.org",
  email: "An email address",
};

export function ParameterField({
  connectorId,
  spec,
  value,
  onChange,
}: {
  connectorId: string;
  spec: ParameterSpec;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const id = `param-${connectorId}-${spec.name}`;
  if (spec.kind === "choice" && spec.choices) {
    return (
      <Field label={spec.label} htmlFor={id} hint={spec.choices[String(value)] ?? spec.description}>
        <Select id={id} value={String(value)} onChange={(event) => onChange(event.target.value)}>
          {Object.entries(spec.choices).map(([choice]) => (
            <option key={choice} value={choice}>
              {humanize(choice)}
            </option>
          ))}
        </Select>
      </Field>
    );
  }
  if (spec.kind === "multi_choice" && spec.choices) {
    const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
    return (
      <fieldset className="md:col-span-4">
        <legend className="text-sm font-medium">{spec.label}</legend>
        <p className="mb-2 text-xs text-muted">
          {spec.description} {selected.size} selected.
        </p>
        <div className="grid max-h-56 gap-1 overflow-y-auto rounded-md border border-line p-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(spec.choices).map(([choice, detail]) => {
            const checkboxId = `${id}-${choice}`;
            return (
              <label key={choice} htmlFor={checkboxId} className="flex items-start gap-2" title={detail}>
                <input
                  id={checkboxId}
                  type="checkbox"
                  checked={selected.has(choice)}
                  onChange={(event) => {
                    const next = new Set(selected);
                    if (event.target.checked) next.add(choice);
                    else next.delete(choice);
                    onChange(Object.keys(spec.choices ?? {}).filter((item) => next.has(item)));
                  }}
                />
                <span>{choice}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
    );
  }
  if (spec.kind === "boolean") {
    return (
      <label htmlFor={id} className="flex items-center gap-2 text-sm md:col-span-2">
        <input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span>
          {spec.label} <span className="text-xs text-muted">{spec.description}</span>
        </span>
      </label>
    );
  }
  return (
    <Field label={spec.label} htmlFor={id} hint={spec.description}>
      <TextInput
        id={id}
        type="number"
        min={spec.minimum ?? undefined}
        max={spec.maximum ?? undefined}
        value={String(value)}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  );
}

function NewQueryForm({ connectors, onCreated }: { connectors: ConnectorDescriptor[]; onCreated: () => Promise<void> }) {
  const { apiBase } = useCase();
  const { mutate } = useSession();
  const [connectorId, setConnectorId] = useState(connectors.find((c) => !c.synthetic)?.connector_id ?? connectors[0]?.connector_id ?? "");
  const connector = connectors.find((item) => item.connector_id === connectorId) ?? connectors[0];
  const [inputType, setInputType] = useState(connector?.supported_input_types[0] ?? "");
  const [parameters, setParameters] = useState<Record<string, unknown>>(connector ? defaultParameters(connector) : {});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mode = useMemo(() => (connector ? collectionMode(connector.collection_mode) : null), [connector]);

  if (!connector || !mode) return <EmptyState>No connectors are installed.</EmptyState>;

  function selectConnector(id: string) {
    const next = connectors.find((item) => item.connector_id === id);
    if (!next) return;
    setConnectorId(id);
    setInputType(next.supported_input_types[0] ?? "");
    setParameters(defaultParameters(next));
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!connector) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/saved-queries`, {
        body: {
          name: String(form.get("name") ?? ""),
          input_type: inputType,
          input_value: String(form.get("input_value") ?? ""),
          connector_ids: [connector.connector_id],
          parameters: changedParameters(connector.parameters, parameters),
          limits: {
            max_pages: Math.min(Number(form.get("max_pages")), connector.max_pages),
            max_items_per_page: connector.synthetic ? 5 : connector.max_items_per_page,
          },
        },
      });
      formElement.reset();
      setParameters(defaultParameters(connector));
      await onCreated();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const missingCredentials = connector.credentials.filter((credential) => credential.required && !credential.configured);
  return (
    <form onSubmit={create} className="space-y-3">
      {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Source" htmlFor="query-connector">
          <Select id="query-connector" value={connector.connector_id} onChange={(event) => selectConnector(event.target.value)}>
            {connectors.map((item) => (
              <option key={item.connector_id} value={item.connector_id}>
                {item.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Name" htmlFor="query-name">
          <TextInput id="query-name" name="name" required maxLength={200} />
        </Field>
        <Field label="Input type" htmlFor="query-input-type">
          <Select id="query-input-type" value={inputType} onChange={(event) => setInputType(event.target.value)}>
            {connector.supported_input_types.map((type) => (
              <option key={type} value={type}>
                {humanize(type)}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Input value"
          htmlFor="query-input-value"
          hint={connector.synthetic ? "Used only to generate synthetic data." : INPUT_HINTS[inputType]}
        >
          <TextInput id="query-input-value" name="input_value" required maxLength={1000} />
        </Field>
        {connector.max_pages > 1 ? (
          <Field label="Maximum pages" htmlFor="query-max-pages" hint={`At most ${connector.max_pages}.`}>
            <TextInput id="query-max-pages" name="max_pages" type="number" min={1} max={connector.max_pages} defaultValue={Math.min(3, connector.max_pages)} />
          </Field>
        ) : (
          <input type="hidden" name="max_pages" value="1" />
        )}
        {connector.parameters.map((spec) => (
          <ParameterField
            key={`${connector.connector_id}-${spec.name}`}
            connectorId={connector.connector_id}
            spec={spec}
            value={parameters[spec.name] ?? spec.default}
            onChange={(value) => setParameters((current) => ({ ...current, [spec.name]: value }))}
          />
        ))}
      </div>
      <div aria-live="polite">
        <Notice tone={connector.synthetic ? "neutral" : "warn"}>
          <strong>{mode.label}:</strong> {mode.explanation} {connector.synthetic ? "" : VERIFICATION_TEXT[connector.verification_status] + "."}{" "}
          Credentials: {connector.credential_requirements} Cost: {connector.cost_model ?? "none"}.
        </Notice>
      </div>
      {missingCredentials.length > 0 ? (
        <p role="alert" className="text-sm text-bad">
          This source needs {missingCredentials.map((credential) => credential.label).join(", ")}. An administrator can add it on the{" "}
          <Link href="/sources" className="underline">
            Sources
          </Link>{" "}
          page.
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={busy} aria-busy={busy}>
        Save query
      </Button>
    </form>
  );
}

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
  const names = useMemo(
    () => Object.fromEntries((connectors.data ?? []).map((connector) => [connector.connector_id, connector])),
    [connectors.data],
  );

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
      {writable ? (
        <Section
          title="New saved query"
          description="A saved query is a reusable definition. Each run stores its own snapshot of these parameters."
          actions={
            <Link href="/sources" className="text-sm text-accent hover:underline">
              Compare sources
            </Link>
          }
        >
          {connectors.state === "error" ? <ErrorNotice error={connectors.error} /> : null}
          {connectors.data ? (
            <NewQueryForm connectors={connectors.data} onCreated={async () => void (await Promise.all([queries.reload(), refreshCase()]))} />
          ) : (
            <LoadingState label="Loading sources…" />
          )}
        </Section>
      ) : null}

      <Section title="Saved queries">
        {queries.state === "error" ? <ErrorNotice error={queries.error} onRetry={() => void queries.reload()} /> : null}
        {queries.state === "loading" && !queries.data ? <LoadingState /> : null}
        {queries.data && queries.data.items.length === 0 ? <EmptyState>No saved queries yet.</EmptyState> : null}
        {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
        <ul className="divide-y divide-line text-sm">
          {queries.data?.items.map((query) => {
            const parameterText = Object.entries(query.parameters)
              .map(([key, value]) => `${humanize(key)}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
              .join(" · ");
            return (
              <li key={query.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{query.name}</span>
                    {query.synthetic ? <SyntheticBadge /> : null}
                    <span className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">{collectionMode(query.collection_mode).label}</span>
                  </div>
                  <div className="text-xs text-muted">
                    {query.connector_ids.map((id) => names[id]?.display_name ?? id).join(", ")} · {humanize(query.input_type)}:{" "}
                    <span className="font-mono">{query.input_value}</span>
                    {parameterText ? ` · ${parameterText}` : ""} · up to {query.limits.max_pages ?? "—"} page(s) · {query.run_counter} run(s)
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
            );
          })}
        </ul>
        {writable ? <Notice>Deleting a saved query keeps its previous runs, snapshots and evidence.</Notice> : null}
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
