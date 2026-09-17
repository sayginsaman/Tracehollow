"use client";

import { CircleAlert, Plus, Radar, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { changeCountsText, describeSchedule, occurrenceSummary, STATUS_REASONS } from "@/lib/monitoring";
import { useResource } from "@/lib/session-context";
import type { ConnectorDescriptor, Monitor, Page, SavedQuery } from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import {
  Button,
  ButtonLink,
  EmptyState,
  ErrorNotice,
  IconButton,
  LoadingState,
  MonitorStatusBadge,
  Notice,
  PageHeader,
  Panel,
  RunOutcomeBadge,
  StatusBadge,
  SyntheticBadge,
  Tag,
  Timestamp,
} from "../ui";
import { BudgetUsageList } from "./BudgetUsage";
import { MonitorForm } from "./MonitorForm";

export function connectorNameMap(connectors: ConnectorDescriptor[] | null): Record<string, { display_name: string; synthetic: boolean }> {
  return Object.fromEntries((connectors ?? []).map((item) => [item.connector_id, { display_name: item.display_name, synthetic: item.synthetic }]));
}

function MonitorRow({ monitor, base }: { monitor: Monitor; base: string }) {
  const last = monitor.last_occurrence;
  const monitorUsage = monitor.budget_usage.filter((item) => item.scope_type === "monitor");
  return (
    <li className="grid gap-x-8 gap-y-3 px-4 py-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`${base}/monitors/${monitor.id}`} className="font-medium break-words text-ink hover:underline">
            {monitor.name}
          </Link>
          <MonitorStatusBadge status={monitor.status} />
          {monitor.collects_live ? <Tag icon={Radar}>External sources</Tag> : <SyntheticBadge />}
        </div>
        <p className="text-sm break-words text-muted">
          {describeSchedule(monitor.schedule, monitor.timezone)} · {monitor.saved_query_name}
        </p>
        {monitor.status === "enabled" ? (
          <p className="text-sm text-muted">
            Next run <Timestamp value={monitor.next_run_at} />
          </p>
        ) : monitor.actions.length === 0 ? (
          // With an action message the reason is part of it; one sentence is enough.
          <p className="text-sm text-muted">{STATUS_REASONS[monitor.status_reason ?? ""] ?? "Not scheduled"}</p>
        ) : null}
        {monitor.actions.length ? (
          <p className="flex items-start gap-1.5 text-sm text-warn">
            <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0">{monitor.actions[0]?.message}</span>
          </p>
        ) : null}
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Last</span>
          {last ? (
            last.status === "dispatched" && last.run_status ? (
              <>
                <RunOutcomeBadge run={{ status: last.run_status, connector_outcomes: last.connector_outcomes }} />
                {last.changes.length ? (
                  <span className="text-muted">{changeCountsText(Object.assign({}, ...last.changes.map((change) => change.counts)))}</span>
                ) : null}
              </>
            ) : (
              <StatusBadge tone="neutral" label={occurrenceSummary(last)} />
            )
          ) : (
            <span className="text-muted">No runs yet</span>
          )}
        </div>
        <BudgetUsageList usage={monitorUsage} compact />
      </div>
    </li>
  );
}

export function MonitorsView({ startCreating = false }: { startCreating?: boolean }) {
  const { apiBase, base, writable } = useCase();
  const monitors = useResource<Page<Monitor>>(`${apiBase}/monitors?limit=100`);
  const queries = useResource<Page<SavedQuery>>(`${apiBase}/saved-queries?limit=100`);
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  const [creating, setCreating] = useState(startCreating);
  const names = useMemo(() => connectorNameMap(connectors.data), [connectors.data]);
  const attention = (monitors.data?.items ?? []).filter((monitor) => monitor.actions.length > 0).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Monitors"
        description="Monitors rerun a saved query on a schedule within limits and a budget, compare each collection with the previous comparable one, and notify about meaningful changes and failures."
        actions={
          writable && !creating ? (
            <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>
              New monitor
            </Button>
          ) : undefined
        }
      />

      {creating && writable ? (
        <Panel title="New monitor" description="Created paused unless you enable it. Nothing is collected until a run is dispatched." actions={<IconButton icon={X} label="Close form" onClick={() => setCreating(false)} />}>
          {queries.state === "error" ? <ErrorNotice error={queries.error} onRetry={() => void queries.reload()} /> : null}
          {!queries.data || !connectors.data ? <LoadingState label="Loading saved queries…" rows={4} /> : null}
          {queries.data && connectors.data && queries.data.items.length === 0 ? (
            <EmptyState title="Save a query first" action={<ButtonLink href={`${base}/queries?new=1`}>New query</ButtonLink>}>
              A monitor reruns a saved query. Create the query with its source, input and limits, then come back.
            </EmptyState>
          ) : null}
          {queries.data?.items.length && connectors.data ? (
            <MonitorForm
              apiBase={apiBase}
              queries={queries.data.items}
              connectorNames={names}
              onSaved={async () => {
                setCreating(false);
                await monitors.reload();
              }}
            />
          ) : null}
        </Panel>
      ) : null}

      {attention ? (
        <Notice tone="warn" title={`${attention} monitor${attention === 1 ? " needs" : "s need"} attention`}>
          Paused, out of budget or failing monitors are marked below; open one for the reason and the action to take.
        </Notice>
      ) : null}

      <Panel title="Monitors in this case" flush>
        {monitors.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={monitors.error} onRetry={() => void monitors.reload()} />
          </div>
        ) : null}
        {!monitors.data && monitors.state === "loading" ? <LoadingState label="Loading monitors…" className="p-4" /> : null}
        {monitors.data && monitors.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={Radar} title="No monitors yet" action={writable ? <Button icon={Plus} onClick={() => setCreating(true)}>New monitor</Button> : undefined}>
              Monitoring reruns a saved query on a schedule and reports what is new, changed or no longer observed, without ever calling an item deleted when a
              collection was incomplete.
            </EmptyState>
          </div>
        ) : null}
        <ul className="divide-y divide-line">
          {monitors.data?.items.map((monitor) => (
            <MonitorRow key={monitor.id} monitor={monitor} base={base} />
          ))}
        </ul>
      </Panel>
    </div>
  );
}
