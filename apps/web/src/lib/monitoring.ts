import type { BudgetUsage, Monitor, MonitorSchedule, Occurrence } from "./workspace-types";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function everyMinutes(minutes: number): string {
  if (minutes % (60 * 24) === 0) {
    const days = minutes / (60 * 24);
    return days === 1 ? "Every day" : `Every ${days} days`;
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return hours === 1 ? "Every hour" : `Every ${hours} hours`;
  }
  return minutes === 1 ? "Every minute" : `Every ${minutes} minutes`;
}

/** "Every 6 hours", "Daily at 09:30 (Europe/Istanbul)", "Mon, Fri at 09:00 (UTC)". */
export function describeSchedule(schedule: MonitorSchedule, timezone: string): string {
  if (schedule.kind === "interval") return everyMinutes(schedule.every_minutes ?? 60);
  const days = schedule.kind === "weekly" ? (schedule.weekdays ?? []).map((day) => WEEKDAYS[day] ?? String(day)).join(", ") : "Daily";
  return `${days} at ${schedule.time ?? "?"} (${timezone})`;
}

/** What a schedule does around daylight-saving changes, in one sentence. */
export function scheduleTimeRule(schedule: MonitorSchedule): string {
  if (schedule.kind === "interval") {
    return "Counted in elapsed time from when the schedule was set; daylight-saving changes never shift it.";
  }
  return "Wall-clock time in the timezone. A time skipped when clocks go forward runs that much later; a time repeated when clocks go back runs once, the first time.";
}

export const SKIP_REASONS: Record<string, string> = {
  overlap: "Skipped: the previous run was still queued or running",
  budget_exhausted: "Skipped: budget used up for the period",
  authorization_lost: "Skipped: the authorizing analyst lost access",
  query_changed: "Skipped: the saved query changed",
  query_invalid: "Skipped: the saved query is no longer valid",
  case_inactive: "Skipped: the case is not active",
  missed: "Missed while no scheduler was running",
};

export const STATUS_REASONS: Record<string, string> = {
  created: "Created paused",
  manual: "Paused by an analyst",
  query_changed: "Paused: saved query changed",
  authorization_lost: "Paused: authorizing analyst lost access",
  repeated_failures: "Paused after repeated failures",
  case_archived: "Paused: case archived",
  query_invalid: "Paused: saved query no longer valid",
  invalid_schedule: "Paused: schedule no longer valid",
  case_deleting: "Disabled: case is being deleted",
};

export function occurrenceSummary(occurrence: Occurrence): string {
  if (occurrence.status === "skipped") return SKIP_REASONS[occurrence.skip_reason ?? ""] ?? "Skipped";
  return occurrence.kind === "manual" ? "Run started by an analyst" : "Run dispatched on schedule";
}

const PERIOD_WORDS: Record<string, string> = { day: "today (UTC)", week: "this week (UTC)", month: "this month (UTC)", run: "in one run" };
const SCOPE_WORDS: Record<string, string> = { case: "Case", monitor: "Monitor", query_run: "Run" };

/** "Monitor: 32 of 100 requests used today (UTC); 4 estimated". */
export function describeUsage(usage: BudgetUsage): string {
  const unit = usage.metric === "requests" ? "requests" : "provider units";
  const used = usage.consumed_units + usage.estimated_units + usage.reserved_units;
  const parts = [`${SCOPE_WORDS[usage.scope_type] ?? usage.scope_type}: ${used} of ${usage.limit_units} ${unit} used ${PERIOD_WORDS[usage.period] ?? usage.period}`];
  if (usage.estimated_units) parts.push(`${usage.estimated_units} estimated`);
  if (usage.reserved_units) parts.push(`${usage.reserved_units} in flight`);
  return parts.join("; ");
}

export function usageFraction(usage: BudgetUsage): number {
  if (usage.limit_units <= 0) return 1;
  return Math.min(1, (usage.consumed_units + usage.estimated_units + usage.reserved_units) / usage.limit_units);
}

export function changeCountsText(counts: Record<string, number>): string {
  const labels: [string, string][] = [
    ["new", "new"],
    ["changed", "changed"],
    ["not_observed", "no longer observed"],
    ["conflicting", "conflicting"],
    ["unknown", "unknown"],
  ];
  const parts = labels.filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label}`);
  return parts.length ? parts.join(", ") : "no item differences";
}

export function needsAttention(monitor: Monitor): boolean {
  return monitor.actions.length > 0;
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function timezoneOptions(current: string): string[] {
  let zones: string[] = [];
  try {
    zones = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    zones = [];
  }
  const all = new Set(["UTC", ...zones, current]);
  return Array.from(all).sort((a, b) => (a === "UTC" ? -1 : b === "UTC" ? 1 : a.localeCompare(b)));
}
