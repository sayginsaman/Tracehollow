"use client";

import { Play, Plug, Plus, RotateCw, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { changedParameters, collectionMode, defaultParameters, VERIFICATION_TEXT } from "@/lib/connectors";
import { describeError, formatUtcShort } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ConnectorDescriptor, Page, ParameterSpec, QueryRun, QueryRunDetail, SavedQuery } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  ChoiceField,
  ConfirmAction,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  FieldGroup,
  FormError,
  IconButton,
  LoadingState,
  Mono,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  RunOutcomeBadge,
  RunStatusBadge,
  Select,
  SyntheticBadge,
  Tag,
  Td,
  TextInput,
  Th,
  Tr,
  humanize,
  plural,
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
      <FieldGroup legend={spec.label} hint={`${spec.description} ${selected.size} selected.`} className="md:col-span-2 xl:col-span-3">
        <div className="grid max-h-56 gap-2 overflow-y-auto rounded-md border border-line p-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(spec.choices).map(([choice, detail]) => (
            <ChoiceField
              key={choice}
              id={`${id}-${choice}`}
              title={detail}
              checked={selected.has(choice)}
              onChange={(event) => {
                const next = new Set(selected);
                if (event.target.checked) next.add(choice);
                else next.delete(choice);
                onChange(Object.keys(spec.choices ?? {}).filter((item) => next.has(item)));
              }}
              label={choice}
            />
          ))}
        </div>
      </FieldGroup>
    );
  }
  if (spec.kind === "boolean") {
    return (
      <ChoiceField
        id={id}
        checked={Boolean(value)}
        onChange={(event) => onChange(event.target.checked)}
        label={spec.label}
        description={spec.description}
        className="self-end md:col-span-2"
      />
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

function NewQueryForm({ connectors, onCreated, onClose }: { connectors: ConnectorDescriptor[]; onCreated: () => Promise<void>; onClose: () => void }) {
  const { apiBase } = useCase();
  const { mutate } = useSession();
  const [connectorId, setConnectorId] = useState(connectors.find((c) => !c.synthetic)?.connector_id ?? connectors[0]?.connector_id ?? "");
  const connector = connectors.find((item) => item.connector_id === connectorId) ?? connectors[0];
  const [inputType, setInputType] = useState(connector?.supported_input_types[0] ?? "");
  const [parameters, setParameters] = useState<Record<string, unknown>>(connector ? defaultParameters(connector) : {});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mode = useMemo(() => (connector ? collectionMode(connector.collection_mode) : null), [connector]);

  if (!connector || !mode) return <EmptyState compact>No connectors are installed.</EmptyState>;

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
    <Panel
      title="New saved query"
      description="A saved query is a reusable definition. Each run stores its own snapshot of these parameters."
      actions={
        <>
          <Link href="/sources" className="text-sm text-accent hover:underline">
            Compare sources
          </Link>
          <IconButton icon={X} label="Close form" onClick={onClose} />
        </>
      }
    >
      <form onSubmit={create} className="space-y-5">
        <FormError message={error} />
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Source" htmlFor="query-connector">
            <Select id="query-connector" value={connector.connector_id} onChange={(event) => selectConnector(event.target.value)}>
              {connectors.map((item) => (
                <option key={item.connector_id} value={item.connector_id}>
                  {item.display_name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Name" htmlFor="query-name" hint="How this query appears in lists and reports.">
            <TextInput id="query-name" name="name" required maxLength={200} />
          </Field>
        </div>

        <div aria-live="polite">
          <Notice tone={connector.synthetic ? "neutral" : "warn"} icon={Plug}>
            <strong>{mode.label}:</strong> {mode.explanation} {connector.synthetic ? "" : VERIFICATION_TEXT[connector.verification_status] + "."}{" "}
            Credentials: {connector.credential_requirements} Cost: {connector.cost_model ?? "none"}.
          </Notice>
        </div>
        {missingCredentials.length > 0 ? (
          <p role="alert" className="rounded-md border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">
            This source needs {missingCredentials.map((credential) => credential.label).join(", ")}. An administrator can add it on the{" "}
            <Link href="/sources" className="font-medium underline">
              Sources
            </Link>{" "}
            page. Until then runs stop with access or setup required, and nothing is collected.
          </p>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Field label="Input type" htmlFor="query-input-type">
            <Select id="query-input-type" value={inputType} onChange={(event) => setInputType(event.target.value)}>
              {connector.supported_input_types.map((type) => (
                <option key={type} value={type}>
                  {humanize(type)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Input value" htmlFor="query-input-value" hint={connector.synthetic ? "Used only to generate synthetic data." : INPUT_HINTS[inputType]}>
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
        <Button type="submit" variant="primary" disabled={busy} busy={busy}>
          Save query
        </Button>
      </form>
    </Panel>
  );
}

export function QueriesView({ startCreating = false }: { startCreating?: boolean }) {
  const { apiBase, base, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  const queries = useResource<Page<SavedQuery>>(`${apiBase}/saved-queries?limit=100`);
  const [runOffset, setRunOffset] = useState(0);
  const runs = useResource<Page<QueryRun>>(`${apiBase}/runs?limit=${RUN_PAGE_SIZE}&offset=${runOffset}`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState<boolean | null>(startCreating ? true : null);
  const names = useMemo(
    () => Object.fromEntries((connectors.data ?? []).map((connector) => [connector.connector_id, connector])),
    [connectors.data],
  );
  const showForm = writable && (adding ?? queries.data?.total === 0);

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
      <PageHeader
        title="Queries & runs"
        description="Saved queries define what to collect from which source. Each run executes one once and keeps its own parameter snapshot, outcome and evidence."
        actions={
          writable && !showForm ? (
            <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>
              New query
            </Button>
          ) : undefined
        }
      />

      {showForm ? (
        connectors.state === "error" ? (
          <ErrorNotice error={connectors.error} onRetry={() => void connectors.reload()} />
        ) : connectors.data ? (
          <NewQueryForm
            connectors={connectors.data}
            onCreated={async () => {
              // Keep the form open for the next definition; the new query is listed below with its Run action.
              setAdding(true);
              await Promise.all([queries.reload(), refreshCase()]);
            }}
            onClose={() => setAdding(false)}
          />
        ) : (
          <LoadingState label="Loading sources…" rows={4} />
        )
      ) : null}

      <Panel title="Saved queries" description={writable ? "Deleting a saved query keeps its previous runs, snapshots and evidence." : undefined} flush>
        {queries.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={queries.error} onRetry={() => void queries.reload()} />
          </div>
        ) : null}
        {queries.state === "loading" && !queries.data ? <LoadingState label="Loading saved queries…" className="p-4" /> : null}
        {queries.data && queries.data.items.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No saved queries yet.</p> : null}
        <ActionError message={error} className="m-4" />
        <ul className="divide-y divide-line">
          {queries.data?.items.map((query) => {
            const parameterText = Object.entries(query.parameters)
              .map(([key, value]) => `${humanize(key)}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
              .join(" · ");
            return (
              <li key={query.id} className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 px-4 py-3.5">
                <div className="min-w-0 flex-1 basis-80 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium break-words text-ink">{query.name}</span>
                    {query.synthetic ? <SyntheticBadge /> : null}
                    <Tag>{collectionMode(query.collection_mode).label}</Tag>
                  </div>
                  <p className="text-sm break-words text-muted">
                    {query.connector_ids.map((id) => names[id]?.display_name ?? id).join(", ")} · {humanize(query.input_type)}{" "}
                    <Mono className="text-ink">{query.input_value}</Mono>
                  </p>
                  <p className="text-xs break-words text-muted">
                    {parameterText ? `${parameterText} · ` : ""}up to {plural(Number(query.limits.max_pages ?? 1), "page")} · {plural(query.run_counter, "run")}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {query.last_run_id && query.last_run_status ? (
                    <Link href={`${base}/runs/${query.last_run_id}`} className="flex items-center gap-1.5 rounded text-xs text-muted hover:text-ink">
                      Last run <RunStatusBadge status={query.last_run_status} />
                    </Link>
                  ) : null}
                  {writable ? (
                    <>
                      <Button size="sm" variant="primary" icon={Play} onClick={() => void runQuery(query.id)} disabled={busy === query.id} busy={busy === query.id}>
                        Run
                      </Button>
                      <ConfirmAction label="Delete" confirmLabel="Delete query" message="Delete this saved query?" onConfirm={() => void deleteQuery(query.id)} busy={busy === query.id} />
                    </>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      </Panel>

      <Panel
        title="Run history"
        description="Every execution with its plain-language result. Open a run for per-source outcomes, coverage and collected evidence."
        flush
        actions={
          <Button size="sm" icon={RotateCw} onClick={() => void runs.reload()}>
            Refresh
          </Button>
        }
      >
        {runs.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={runs.error} onRetry={() => void runs.reload()} />
          </div>
        ) : null}
        {runs.state === "loading" && !runs.data ? <LoadingState label="Loading runs…" className="p-4" /> : null}
        {runs.data && runs.data.items.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No runs yet. Run a saved query to collect.</p> : null}
        {runs.data && runs.data.items.length > 0 ? (
          <>
            <DataTable caption="Run history" minWidth="46rem">
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Result</Th>
                  <Th>Queued (UTC)</Th>
                  <Th className="text-right">Evidence</Th>
                  <Th className="text-right">Observations</Th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <Tr key={run.id}>
                    <Td>
                      <Link href={`${base}/runs/${run.id}`} className="font-medium break-words text-accent hover:underline">
                        {run.saved_query_name ?? run.parameters_snapshot.saved_query_name ?? "Query"} #{run.run_number}
                      </Link>
                      {run.synthetic ? (
                        <span className="ml-2 inline-block align-middle">
                          <SyntheticBadge />
                        </span>
                      ) : null}
                    </Td>
                    <Td>
                      <RunOutcomeBadge run={run} />
                    </Td>
                    <Td className="whitespace-nowrap text-muted">{formatUtcShort(run.queued_at)}</Td>
                    <Td className="text-right tabular-nums">{run.evidence_count}</Td>
                    <Td className="text-right tabular-nums">{run.observation_count}</Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
            <Pagination total={runs.data.total} limit={RUN_PAGE_SIZE} offset={runOffset} onChange={setRunOffset} className="border-t border-line" />
          </>
        ) : null}
      </Panel>

    </div>
  );
}
