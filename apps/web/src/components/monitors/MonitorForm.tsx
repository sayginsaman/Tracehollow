"use client";

import { Radar } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { browserTimezone, describeSchedule, scheduleInSentence, scheduleTimeRule, timezoneOptions, WEEKDAY_NAMES } from "@/lib/monitoring";
import { useSession } from "@/lib/session-context";
import type { Monitor, MonitorSchedule, SavedQuery } from "@/lib/workspace-types";

import { Button, ChoiceField, Disclosure, Field, FieldGroup, FormError, Notice, Select, TextInput, plural } from "../ui";

type Kind = MonitorSchedule["kind"];

interface Values {
  name: string;
  savedQueryId: string;
  connectorIds: string[];
  kind: Kind;
  everyHours: number;
  time: string;
  weekdays: number[];
  timezone: string;
  missedRunPolicy: "run_latest" | "skip";
  maxPages: number;
  maxItemsPerPage: number;
  maxRequestsPerRun: number;
  maxItemsPerRun: number;
  maxRunMinutes: number;
  budgetPeriod: "day" | "week" | "month";
  budgetRequests: number;
  keepLastRuns: string;
  maxAgeDays: string;
  onChange: boolean;
  onFailure: boolean;
  onBudget: boolean;
  onCompletion: boolean;
  recipients: "case_analysts" | "all_members";
}

function initialValues(queries: SavedQuery[], monitor?: Monitor): Values {
  const query = monitor ? queries.find((item) => item.id === monitor.saved_query_id) : queries[0];
  const queryPages = Number(query?.limits.max_pages ?? 1);
  const queryItems = Number(query?.limits.max_items_per_page ?? 5);
  return {
    name: monitor?.name ?? "",
    savedQueryId: monitor?.saved_query_id ?? query?.id ?? "",
    connectorIds: monitor?.connector_ids ?? query?.connector_ids ?? [],
    kind: monitor?.schedule.kind ?? "interval",
    everyHours: monitor?.schedule.every_minutes ? monitor.schedule.every_minutes / 60 : 24,
    time: monitor?.schedule.time ?? "09:00",
    weekdays: monitor?.schedule.weekdays ?? [0],
    timezone: monitor?.timezone ?? browserTimezone(),
    missedRunPolicy: monitor?.missed_run_policy ?? "run_latest",
    maxPages: monitor?.scope.max_pages ?? queryPages,
    maxItemsPerPage: monitor?.scope.max_items_per_page ?? queryItems,
    maxRequestsPerRun: monitor?.limits.max_requests_per_run ?? 25,
    maxItemsPerRun: monitor?.limits.max_items_per_run ?? 200,
    maxRunMinutes: monitor ? Math.round(monitor.limits.max_run_seconds / 60) : 10,
    budgetPeriod: monitor?.budget.period ?? "day",
    budgetRequests: monitor?.budget.max_requests ?? 100,
    keepLastRuns: monitor?.retention.keep_last_runs ? String(monitor.retention.keep_last_runs) : "",
    maxAgeDays: monitor?.retention.max_age_days ? String(monitor.retention.max_age_days) : "",
    onChange: monitor?.notify.on_change ?? true,
    onFailure: monitor?.notify.on_failure ?? true,
    onBudget: monitor?.notify.on_budget_exhausted ?? true,
    onCompletion: monitor?.notify.on_completion ?? false,
    recipients: monitor?.notify.recipients ?? "case_analysts",
  };
}

function schedulePayload(values: Values): MonitorSchedule {
  if (values.kind === "interval") return { kind: "interval", every_minutes: Math.round(values.everyHours * 60) };
  if (values.kind === "daily") return { kind: "daily", time: values.time };
  return { kind: "weekly", time: values.time, weekdays: values.weekdays };
}

/** Creates a monitor from a saved query, or edits one. Monitors are created paused unless enabled here. */
export function MonitorForm({
  apiBase,
  queries,
  connectorNames,
  monitor,
  onSaved,
}: {
  apiBase: string;
  queries: SavedQuery[];
  connectorNames: Record<string, { display_name: string; synthetic: boolean }>;
  monitor?: Monitor;
  onSaved: (monitor: Monitor) => Promise<void> | void;
}) {
  const { mutate } = useSession();
  const [values, setValues] = useState<Values>(() => initialValues(queries, monitor));
  const [enable, setEnable] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const query = queries.find((item) => item.id === values.savedQueryId);
  const zones = useMemo(() => timezoneOptions(values.timezone), [values.timezone]);
  const live = values.connectorIds.some((id) => !connectorNames[id]?.synthetic);
  const queryPages = Number(query?.limits.max_pages ?? 10);
  const queryItems = Number(query?.limits.max_items_per_page ?? 5000);
  const editing = Boolean(monitor);

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function chooseQuery(id: string) {
    const next = queries.find((item) => item.id === id);
    setValues((current) => ({
      ...current,
      savedQueryId: id,
      connectorIds: next?.connector_ids ?? [],
      maxPages: Math.min(current.maxPages, Number(next?.limits.max_pages ?? 1)),
      maxItemsPerPage: Math.min(current.maxItemsPerPage, Number(next?.limits.max_items_per_page ?? 5)),
    }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      name: values.name,
      connector_ids: values.connectorIds,
      schedule: schedulePayload(values),
      timezone: values.timezone,
      missed_run_policy: values.missedRunPolicy,
      scope: { max_pages: values.maxPages, max_items_per_page: values.maxItemsPerPage },
      limits: {
        max_requests_per_run: values.maxRequestsPerRun,
        max_items_per_run: values.maxItemsPerRun,
        max_run_seconds: values.maxRunMinutes * 60,
      },
      budget: { period: values.budgetPeriod, max_requests: values.budgetRequests, max_provider_units: null },
      retention: {
        keep_last_runs: values.keepLastRuns ? Number(values.keepLastRuns) : null,
        max_age_days: values.maxAgeDays ? Number(values.maxAgeDays) : null,
      },
      notify: {
        on_change: values.onChange,
        on_failure: values.onFailure,
        on_budget_exhausted: values.onBudget,
        on_completion: values.onCompletion,
        recipients: values.recipients,
      },
    };
    try {
      const saved = editing
        ? await mutate<Monitor>(`${apiBase}/monitors/${monitor?.id}`, { method: "PATCH", body })
        : await mutate<Monitor>(`${apiBase}/monitors`, {
            body: { ...body, saved_query_id: values.savedQueryId, enable, acknowledge_recurring_collection: acknowledged },
          });
      await onSaved(saved);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const schedulePreview = describeSchedule(schedulePayload(values), values.timezone);
  const prefix = editing ? "edit-monitor" : "monitor";
  return (
    <form onSubmit={submit} className="space-y-6">
      <FormError message={error} />
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name" htmlFor={`${prefix}-name`} hint="Shown in notifications inside Tracehollow. It is never sent to external destinations.">
          <TextInput id={`${prefix}-name`} required maxLength={200} value={values.name} onChange={(event) => set("name", event.target.value)} />
        </Field>
        <Field label="Saved query" htmlFor={`${prefix}-query`} hint={editing ? "A monitor stays with its saved query." : "What to collect. Editing the query later pauses this monitor for review."}>
          <Select id={`${prefix}-query`} value={values.savedQueryId} disabled={editing} onChange={(event) => chooseQuery(event.target.value)} required>
            {queries.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {query && query.connector_ids.length > 1 ? (
        <FieldGroup legend="Sources" hint="The monitor may use fewer sources than the saved query.">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {query.connector_ids.map((id) => (
              <ChoiceField
                key={id}
                label={connectorNames[id]?.display_name ?? id}
                checked={values.connectorIds.includes(id)}
                onChange={(event) =>
                  set("connectorIds", event.target.checked ? [...values.connectorIds, id] : values.connectorIds.filter((item) => item !== id))
                }
              />
            ))}
          </div>
        </FieldGroup>
      ) : null}

      <FieldGroup legend="Schedule" hint={`${schedulePreview}. ${scheduleTimeRule(schedulePayload(values))}`}>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field label="Repeat" htmlFor={`${prefix}-kind`}>
            <Select id={`${prefix}-kind`} value={values.kind} onChange={(event) => set("kind", event.target.value as Kind)}>
              <option value="interval">Every few hours</option>
              <option value="daily">Daily at a time</option>
              <option value="weekly">Weekly on days</option>
            </Select>
          </Field>
          {values.kind === "interval" ? (
            <Field label="Every (hours)" htmlFor={`${prefix}-hours`} hint="The installation sets the shortest allowed interval.">
              <TextInput id={`${prefix}-hours`} type="number" min={0.25} step={0.25} max={2160} value={values.everyHours} onChange={(event) => set("everyHours", Number(event.target.value))} />
            </Field>
          ) : (
            <Field label="Time" htmlFor={`${prefix}-time`}>
              <TextInput id={`${prefix}-time`} type="time" value={values.time} onChange={(event) => set("time", event.target.value)} required />
            </Field>
          )}
          <Field label="Timezone" htmlFor={`${prefix}-timezone`}>
            <Select id={`${prefix}-timezone`} value={values.timezone} onChange={(event) => set("timezone", event.target.value)}>
              {zones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="After downtime" htmlFor={`${prefix}-missed`} hint="Never more than one catch-up run.">
            <Select id={`${prefix}-missed`} value={values.missedRunPolicy} onChange={(event) => set("missedRunPolicy", event.target.value as Values["missedRunPolicy"])}>
              <option value="run_latest">Run once for the latest missed time</option>
              <option value="skip">Skip missed times</option>
            </Select>
          </Field>
        </div>
        {values.kind === "weekly" ? (
          <fieldset className="mt-3">
            <legend className="sr-only">Weekdays</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {WEEKDAY_NAMES.map((day, index) => (
                <ChoiceField
                  key={day}
                  label={day}
                  checked={values.weekdays.includes(index)}
                  onChange={(event) =>
                    set("weekdays", event.target.checked ? [...values.weekdays, index].sort() : values.weekdays.filter((item) => item !== index))
                  }
                />
              ))}
            </div>
          </fieldset>
        ) : null}
      </FieldGroup>

      <FieldGroup legend="Scope and limits" hint="Bounds for every run. Scope cannot exceed the saved query's own limits.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <Field label="Pages" htmlFor={`${prefix}-pages`} hint={`At most ${queryPages}.`}>
            <TextInput id={`${prefix}-pages`} type="number" min={1} max={queryPages} value={values.maxPages} onChange={(event) => set("maxPages", Number(event.target.value))} />
          </Field>
          <Field label="Items per page" htmlFor={`${prefix}-items-page`} hint={`At most ${queryItems}.`}>
            <TextInput id={`${prefix}-items-page`} type="number" min={1} max={queryItems} value={values.maxItemsPerPage} onChange={(event) => set("maxItemsPerPage", Number(event.target.value))} />
          </Field>
          <Field label="Requests per run" htmlFor={`${prefix}-requests`} hint="Retries and pages count.">
            <TextInput id={`${prefix}-requests`} type="number" min={1} max={10000} value={values.maxRequestsPerRun} onChange={(event) => set("maxRequestsPerRun", Number(event.target.value))} />
          </Field>
          <Field label="Items per run" htmlFor={`${prefix}-items-run`}>
            <TextInput id={`${prefix}-items-run`} type="number" min={1} max={100000} value={values.maxItemsPerRun} onChange={(event) => set("maxItemsPerRun", Number(event.target.value))} />
          </Field>
          <Field label="Minutes per run" htmlFor={`${prefix}-minutes`}>
            <TextInput id={`${prefix}-minutes`} type="number" min={1} max={60} value={values.maxRunMinutes} onChange={(event) => set("maxRunMinutes", Number(event.target.value))} />
          </Field>
        </div>
      </FieldGroup>

      <FieldGroup legend="Budget" hint="Shared by every run of this monitor. Scheduled runs are skipped once it is used up; runs in progress stop before the next request.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field label="Requests" htmlFor={`${prefix}-budget`}>
            <TextInput id={`${prefix}-budget`} type="number" min={1} max={1000000} value={values.budgetRequests} onChange={(event) => set("budgetRequests", Number(event.target.value))} />
          </Field>
          <Field label="Per" htmlFor={`${prefix}-period`} hint="UTC calendar periods.">
            <Select id={`${prefix}-period`} value={values.budgetPeriod} onChange={(event) => set("budgetPeriod", event.target.value as Values["budgetPeriod"])}>
              <option value="day">Day</option>
              <option value="week">Week</option>
              <option value="month">Month</option>
            </Select>
          </Field>
        </div>
      </FieldGroup>

      <FieldGroup legend="Notifications" hint="In-app notifications. Unchanged successful runs are quiet unless completion updates are on.">
        <div className="grid gap-2 md:grid-cols-2">
          <ChoiceField label="Meaningful changes" checked={values.onChange} onChange={(event) => set("onChange", event.target.checked)} />
          <ChoiceField label="Failures that need action" description="Notified when a run starts failing, not on every repeat." checked={values.onFailure} onChange={(event) => set("onFailure", event.target.checked)} />
          <ChoiceField label="Budget used up" checked={values.onBudget} onChange={(event) => set("onBudget", event.target.checked)} />
          <ChoiceField label="Every completed run" checked={values.onCompletion} onChange={(event) => set("onCompletion", event.target.checked)} />
        </div>
        <Field label="Who is notified" htmlFor={`${prefix}-recipients`} className="mt-3 max-w-sm">
          <Select id={`${prefix}-recipients`} value={values.recipients} onChange={(event) => set("recipients", event.target.value as Values["recipients"])}>
            <option value="case_analysts">Analysts of this case</option>
            <option value="all_members">All members, including viewers</option>
          </Select>
        </Field>
      </FieldGroup>

      <Disclosure summary="Retention of this monitor's results (optional)">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Keep the last runs" htmlFor={`${prefix}-keep`} hint="Older runs' evidence can be removed by the case retention job. At least 2; the latest comparison baseline is always kept.">
            <TextInput id={`${prefix}-keep`} type="number" min={2} max={1000} value={values.keepLastRuns} onChange={(event) => set("keepLastRuns", event.target.value)} />
          </Field>
          <Field label="Or remove results older than (days)" htmlFor={`${prefix}-age`}>
            <TextInput id={`${prefix}-age`} type="number" min={1} max={3650} value={values.maxAgeDays} onChange={(event) => set("maxAgeDays", event.target.value)} />
          </Field>
        </div>
      </Disclosure>

      {!editing ? (
        <div className="space-y-3 rounded-md border border-line p-3">
          <ChoiceField
            label="Enable now"
            description="Otherwise the monitor is saved paused and collects nothing until an analyst enables it."
            checked={enable}
            onChange={(event) => setEnable(event.target.checked)}
          />
          {enable && live ? (
            <Notice tone="warn" icon={Radar} title="Recurring collection from external sources">
              <ChoiceField
                label={`I confirm this monitor contacts the selected sources ${scheduleInSentence(schedulePayload(values), values.timezone)}, using up to ${plural(values.budgetRequests, "request")} per ${values.budgetPeriod}.`}
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                className="mt-1 text-ink"
              />
            </Notice>
          ) : null}
        </div>
      ) : null}

      <Button type="submit" variant="primary" busy={busy} disabled={busy || values.connectorIds.length === 0 || (enable && live && !acknowledged)}>
        {editing ? "Save changes" : enable ? "Create and enable" : "Create paused monitor"}
      </Button>
    </form>
  );
}
