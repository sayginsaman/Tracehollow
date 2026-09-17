"use client";

import { Ban, Pause, Play, RotateCw, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { describeError } from "@/lib/messages";
import { changeCountsText, describeSchedule, occurrenceSummary, scheduleTimeRule, STATUS_REASONS } from "@/lib/monitoring";
import { useResource, useSession } from "@/lib/session-context";
import type {
  AvailableDestination,
  ConnectorDescriptor,
  Monitor,
  Occurrence,
  Page,
  PayloadPreview,
  SavedQuery,
  Subscription,
} from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import { usePageCrumb } from "../shell/ShellContext";
import {
  ActionError,
  Button,
  ChangeSetStatusBadge,
  ChoiceField,
  ConfirmAction,
  DataTable,
  Disclosure,
  EmptyState,
  ErrorNotice,
  Field,
  KeyValue,
  LoadingState,
  MonitorStatusBadge,
  Notice,
  OutcomeBadge,
  PageHeader,
  Pagination,
  Panel,
  RunOutcomeBadge,
  Select,
  SyntheticBadge,
  Tag,
  Td,
  Th,
  Timestamp,
  Tr,
  humanize,
  plural,
} from "../ui";
import { BudgetUsageList } from "./BudgetUsage";
import { MonitorForm } from "./MonitorForm";
import { connectorNameMap } from "./MonitorsView";

const OCCURRENCE_PAGE = 20;
const EVENT_TYPES: [string, string][] = [
  ["change_detected", "Meaningful changes"],
  ["action_required", "Failures that need action"],
  ["budget_exhausted", "Budget used up"],
  ["run_completed", "Completed runs"],
];

function OccurrenceTable({ occurrences, base }: { occurrences: Occurrence[]; base: string }) {
  return (
    <DataTable caption="Occurrences of this monitor, newest first" minWidth="52rem">
      <thead>
        <tr>
          <Th>Scheduled for</Th>
          <Th>Result</Th>
          <Th>Sources</Th>
          <Th>Changes</Th>
          <Th>Configuration</Th>
        </tr>
      </thead>
      <tbody>
        {occurrences.map((occurrence) => (
          <Tr key={occurrence.id}>
            <Td className="whitespace-nowrap">
              <Timestamp value={occurrence.scheduled_for} />
              <span className="mt-0.5 block text-xs text-muted">{occurrence.kind === "manual" ? "Run now" : "Scheduled"}</span>
            </Td>
            <Td>
              {occurrence.status === "dispatched" && occurrence.run_status && occurrence.query_run_id ? (
                <Link href={`${base}/runs/${occurrence.query_run_id}`} className="inline-flex flex-col gap-1 rounded hover:underline">
                  <RunOutcomeBadge run={{ status: occurrence.run_status, connector_outcomes: occurrence.connector_outcomes }} />
                  {occurrence.run_error_code === "budget_exhausted" ? <span className="text-xs text-warn">Stopped by a budget</span> : null}
                  {occurrence.run_error_code === "authorization_revoked" ? <span className="text-xs text-warn">Stopped: access revoked</span> : null}
                </Link>
              ) : (
                <span className="text-sm text-muted">{occurrenceSummary(occurrence)}</span>
              )}
              {occurrence.missed_slots ? <span className="mt-1 block text-xs text-muted">{plural(occurrence.missed_slots, "earlier time")} missed before this one</span> : null}
            </Td>
            <Td>
              <div className="flex flex-wrap gap-1">
                {occurrence.connector_outcomes.map((outcome, index) => (
                  <OutcomeBadge key={index} outcome={outcome} />
                ))}
              </div>
            </Td>
            <Td>
              {occurrence.changes.length ? (
                <ul className="space-y-1">
                  {occurrence.changes.map((change) => (
                    <li key={change.change_set_id}>
                      <Link href={`${base}/changes/${change.change_set_id}`} className="inline-flex flex-wrap items-center gap-1.5 rounded text-sm hover:underline">
                        <ChangeSetStatusBadge status={change.status} />
                        {change.status === "changes_detected" || change.status === "unknown" ? (
                          <span className="text-muted">
                            <span className="sr-only">: </span>
                            {changeCountsText(change.counts)}
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-sm text-muted">{occurrence.status === "dispatched" ? "Pending" : "None"}</span>
              )}
            </Td>
            <Td className="text-sm text-muted">v{occurrence.config_version}</Td>
          </Tr>
        ))}
      </tbody>
    </DataTable>
  );
}

function Subscriptions({ monitor }: { monitor: Monitor }) {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const destinations = useResource<AvailableDestination[]>(`${apiBase}/notification-destinations`);
  const subscriptions = useResource<Subscription[]>(`${apiBase}/monitors/${monitor.id}/subscriptions`);
  const [destinationId, setDestinationId] = useState("");
  const [types, setTypes] = useState<string[]>(["change_detected"]);
  const [preview, setPreview] = useState<PayloadPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const selected = destinations.data?.find((item) => item.id === (destinationId || destinations.data?.[0]?.id));

  if (destinations.data && destinations.data.length === 0 && (subscriptions.data?.length ?? 0) === 0) {
    return (
      <Panel title="External notifications" description="Optional. Sends minimal, signed event summaries to a webhook an administrator configured.">
        <p className="text-sm text-muted">
          No webhook destination is available. External notifications are off by default; an administrator can switch the adapter on and add a destination.
        </p>
      </Panel>
    );
  }

  async function add() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/monitors/${monitor.id}/subscriptions`, { body: { destination_id: selected.id, event_types: types } });
      await subscriptions.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setError(null);
    try {
      await mutate(`${apiBase}/monitors/${monitor.id}/subscriptions/${id}`, { method: "DELETE" });
      await subscriptions.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  async function showPreview(id: string) {
    setError(null);
    try {
      setPreview(await mutate<PayloadPreview>(`${apiBase}/monitors/${monitor.id}/subscriptions/${id}/preview`));
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <Panel title="External notifications" description="Webhook deliveries carry identifiers, counts and reason codes only. Names, collected values, evidence and AI text never leave.">
      <div className="space-y-4">
        <ActionError message={error} />
        {subscriptions.data?.length ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {subscriptions.data.map((subscription) => (
              <li key={subscription.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0 text-sm">
                  <p className="font-medium text-ink">
                    {subscription.destination_name} <span className="font-normal text-muted">({subscription.destination_host})</span>
                  </p>
                  <p className="text-muted">
                    {subscription.event_types.map((type) => EVENT_TYPES.find(([key]) => key === type)?.[1] ?? type).join(", ")}
                    {subscription.destination_enabled ? "" : " · destination disabled, nothing is sent"}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => void showPreview(subscription.id)}>
                    Preview payload
                  </Button>
                  {writable ? (
                    <Button size="sm" variant="danger-ghost" onClick={() => void remove(subscription.id)}>
                      Remove
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
        {preview ? (
          <div className="space-y-2">
            <p className="text-sm font-medium text-ink">
              {preview.method} {preview.url}
            </p>
            <pre className="max-h-72 overflow-auto rounded-md bg-sunken p-3 font-mono text-code text-ink">{JSON.stringify({ headers: preview.headers, body: preview.body }, null, 2)}</pre>
            <ul className="list-inside list-disc text-xs text-muted">
              {preview.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {writable && destinations.data?.length ? (
          <div className="grid items-end gap-3 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_auto]">
            <Field label="Destination" htmlFor="subscription-destination">
              <Select id="subscription-destination" value={selected?.id ?? ""} onChange={(event) => setDestinationId(event.target.value)}>
                {destinations.data.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} ({item.host})
                  </option>
                ))}
              </Select>
            </Field>
            <fieldset>
              <legend className="text-sm font-medium text-ink">Events</legend>
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-2">
                {EVENT_TYPES.filter(([key]) => selected?.event_types.includes(key)).map(([key, label]) => (
                  <ChoiceField
                    key={key}
                    label={label}
                    checked={types.includes(key)}
                    onChange={(event) => setTypes(event.target.checked ? [...types, key] : types.filter((item) => item !== key))}
                  />
                ))}
              </div>
            </fieldset>
            <Button icon={Send} onClick={() => void add()} busy={busy} disabled={busy || !selected || types.length === 0}>
              Send to destination
            </Button>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

export function MonitorDetailView({ monitorId }: { monitorId: string }) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const path = `${apiBase}/monitors/${monitorId}`;
  const monitor = useResource<Monitor>(path);
  const [offset, setOffset] = useState(0);
  const occurrences = useResource<Page<Occurrence>>(`${path}/occurrences?limit=${OCCURRENCE_PAGE}&offset=${offset}`);
  const queries = useResource<Page<SavedQuery>>(writable ? `${apiBase}/saved-queries?limit=100` : null);
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  const names = useMemo(() => connectorNameMap(connectors.data), [connectors.data]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  usePageCrumb(monitor.data?.name);

  if (monitor.state === "error" && !monitor.data) return <ErrorNotice error={monitor.error} onRetry={() => void monitor.reload()} />;
  if (!monitor.data) return <LoadingState label="Loading monitor…" rows={6} />;
  const data = monitor.data;

  async function act(action: string, body?: unknown) {
    setBusy(action);
    setError(null);
    try {
      if (action === "delete") {
        await mutate(path, { method: "DELETE" });
        router.push(`${base}/monitors`);
        return;
      }
      await mutate(`${path}/${action}`, body === undefined ? {} : { body });
      await Promise.all([monitor.reload(), occurrences.reload()]);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  const resumeBody = { acknowledge_recurring_collection: acknowledged, adopt_query_changes: data.query_changed };
  const needsAck = data.collects_live && data.status !== "enabled";
  return (
    <div className="space-y-6">
      <PageHeader
        title={data.name}
        meta={
          <>
            <MonitorStatusBadge status={data.status} />
            {data.collects_live ? <Tag>Contacts external sources</Tag> : <SyntheticBadge />}
            <span>Configuration v{data.config_version}</span>
            {data.authorized_by ? <span>Runs authorized by {data.authorized_by}</span> : null}
          </>
        }
        description={data.description || undefined}
        actions={
          writable ? (
            <>
              {data.status !== "disabled" ? (
                <Button icon={Play} onClick={() => void act("runs")} busy={busy === "runs"} disabled={busy !== null || Boolean(data.active_run_id)}>
                  Run now
                </Button>
              ) : null}
              {data.status === "enabled" ? (
                <Button icon={Pause} onClick={() => void act("pause")} busy={busy === "pause"} disabled={busy !== null}>
                  Pause
                </Button>
              ) : null}
            </>
          ) : undefined
        }
      />

      <ActionError message={error} />

      {data.actions.length ? (
        <Notice tone="warn" title="Needs attention">
          <ul className="space-y-1">
            {data.actions.map((action) => (
              <li key={action.code}>{action.message}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      {writable && data.status !== "enabled" ? (
        <Panel title={data.status === "disabled" ? "Enable this monitor" : "Resume this monitor"} description={STATUS_REASONS[data.status_reason ?? ""] ?? undefined}>
          <div className="space-y-3">
            <p className="max-w-[72ch] text-sm text-muted">
              Scheduling continues from the next future time; the paused period is not caught up. Future runs will work under your analyst access.
              {data.query_changed ? " The saved query changed since this monitor was configured: resuming adopts the new definition and change detection starts a new baseline." : ""}
            </p>
            {needsAck ? (
              <ChoiceField
                label={`I confirm this monitor contacts external sources ${describeSchedule(data.schedule, data.timezone).toLowerCase()}, within its limits and budget.`}
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
            ) : null}
            <Button variant="primary" icon={RotateCw} onClick={() => void act("resume", resumeBody)} busy={busy === "resume"} disabled={busy !== null || (needsAck && !acknowledged)}>
              {data.query_changed ? "Adopt query changes and resume" : data.status === "disabled" ? "Enable" : "Resume"}
            </Button>
          </div>
        </Panel>
      ) : null}

      {data.active_run_id ? (
        <Notice
          tone="neutral"
          title="A run is in progress"
          actions={
            <Link href={`${base}/runs/${data.active_run_id}`} className="text-sm font-medium text-accent hover:underline">
              Open run
            </Link>
          }
        >
          Pausing keeps it running. Cancel it from the run page, or disable the monitor to stop future runs and cancel this one.
        </Notice>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Panel title="Schedule and scope">
          <KeyValue
            items={[
              ["Schedule", describeSchedule(data.schedule, data.timezone)],
              ["Time rules", scheduleTimeRule(data.schedule)],
              ["Next run", data.status === "enabled" ? <Timestamp key="next" value={data.next_run_at} /> : (STATUS_REASONS[data.status_reason ?? ""] ?? humanize(data.status))],
              ["Last scheduled", <Timestamp key="last" value={data.last_scheduled_for} fallback="Never" />],
              ["After downtime", data.missed_run_policy === "run_latest" ? "Runs once for the latest missed time" : "Skips missed times"],
              ["Overlapping runs", "A time is skipped while the previous run is still queued or running"],
              ["Saved query", <Link key="query" href={`${base}/queries`} className="text-accent hover:underline">{data.saved_query_name}</Link>],
              ["Sources", data.connector_ids.map((id) => names[id]?.display_name ?? id).join(", ")],
              ["Scope", `${plural(data.scope.max_pages, "page")}, ${data.scope.max_items_per_page} items per page`],
              ["Run limits", `${plural(data.limits.max_requests_per_run, "request")}, ${data.limits.max_items_per_run} items, ${Math.round(data.limits.max_run_seconds / 60)} minutes`],
            ]}
          />
        </Panel>
        <Panel title="Budget" description={`${plural(data.budget.max_requests, "request")} per ${data.budget.period} for this monitor. Requests Tracehollow sends are measured; engine requests and crashed workers count as estimates.`}>
          <BudgetUsageList usage={data.budget_usage} />
        </Panel>
      </div>

      <Panel title="Occurrences" description="Every scheduled time, dispatched or skipped with the reason, and what each run found compared with the previous comparable collection." flush>
        {occurrences.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={occurrences.error} onRetry={() => void occurrences.reload()} />
          </div>
        ) : null}
        {!occurrences.data ? <LoadingState label="Loading occurrences…" className="p-4" /> : null}
        {occurrences.data && occurrences.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState compact>{data.status === "enabled" ? "The first run is dispatched at the next scheduled time." : "No runs yet."}</EmptyState>
          </div>
        ) : null}
        {occurrences.data?.items.length ? <OccurrenceTable occurrences={occurrences.data.items} base={base} /> : null}
        {occurrences.data ? (
          <div className="border-t border-line px-4 py-2">
            <Pagination total={occurrences.data.total} limit={OCCURRENCE_PAGE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Panel>

      {writable ? <Subscriptions monitor={data} /> : null}

      {writable ? (
        <Disclosure summary="Edit configuration">
          {queries.data && connectors.data ? (
            <MonitorForm
              apiBase={apiBase}
              queries={queries.data.items}
              connectorNames={names}
              monitor={data}
              onSaved={async () => {
                await monitor.reload();
              }}
            />
          ) : (
            <LoadingState label="Loading…" />
          )}
          <p className="mt-3 text-xs text-muted">Edits apply to future runs. Runs already queued or running keep the configuration they were created with.</p>
        </Disclosure>
      ) : null}

      {writable ? (
        <Panel title="Stop monitoring" description="Disabling stops future runs and cancels a queued or running run. Collected evidence and history stay." className="border-bad-line">
          <div className="flex flex-wrap gap-2">
            {data.status !== "disabled" ? (
              <ConfirmAction label="Disable monitor" confirmLabel="Disable and cancel active run" message="Disable this monitor and cancel its active run?" onConfirm={() => void act("disable")} busy={busy === "disable"} />
            ) : (
              <ConfirmAction label="Delete monitor" confirmLabel="Delete monitor" message="Delete this disabled monitor? Its runs, evidence and change history stay in the case." onConfirm={() => void act("delete")} busy={busy === "delete"} />
            )}
          </div>
        </Panel>
      ) : (
        <Notice tone="neutral" icon={Ban}>
          Your role in this case is viewer: you can read this monitor and its results, but not run, pause or change it.
        </Notice>
      )}
    </div>
  );
}
