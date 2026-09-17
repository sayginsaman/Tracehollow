"use client";

import { Bell, BellOff, CheckCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { AppNotification, Page } from "@/lib/workspace-types";

import { ActionError, Button, EmptyState, ErrorNotice, LoadingState, PageHeader, Pagination, Panel, SegmentedFilter, StatusBadge, Timestamp, type Tone } from "../ui";

const PAGE = 25;
const SEVERITY: Record<AppNotification["severity"], { tone: Tone; label: string }> = {
  info: { tone: "neutral", label: "Information" },
  warning: { tone: "warn", label: "Warning" },
  action_required: { tone: "bad", label: "Action needed" },
};
const EVENT_LABELS: Record<AppNotification["event_type"], string> = {
  change_detected: "Changes",
  action_required: "Needs action",
  budget_exhausted: "Budget",
  run_completed: "Completed",
};

export function NotificationsView() {
  const { mutate } = useSession();
  const [filter, setFilter] = useState<"all" | "unread">("unread");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const notifications = useResource<Page<AppNotification>>(`/api/v1/notifications?limit=${PAGE}&offset=${offset}${filter === "unread" ? "&unread=true" : ""}`);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await notifications.reload();
      window.dispatchEvent(new Event("tracehollow:notifications-changed"));
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description="Changes, failures that need action and exhausted budgets from monitors in cases you belong to. Each is sent once per event, even when work is retried."
        actions={
          <Button icon={CheckCheck} onClick={() => void act(() => mutate("/api/v1/notifications/read-all"))}>
            Mark all read
          </Button>
        }
      />
      <ActionError message={error} />
      <Panel title="Inbox" flush>
        <div className="border-b border-line px-4 py-3">
          <SegmentedFilter
            label="Show"
            options={[
              { value: "unread", label: "Unread" },
              { value: "all", label: "All" },
            ]}
            value={filter}
            onChange={(value) => {
              setFilter(value);
              setOffset(0);
            }}
          />
        </div>
        {notifications.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={notifications.error} onRetry={() => void notifications.reload()} />
          </div>
        ) : null}
        {!notifications.data && notifications.state === "loading" ? <LoadingState className="p-4" label="Loading notifications…" /> : null}
        {notifications.data && notifications.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={filter === "unread" ? Bell : BellOff} title={filter === "unread" ? "Nothing unread" : "No notifications"}>
              Monitors notify you here about meaningful changes, failures that need action and exhausted budgets, following each monitor&apos;s preferences.
            </EmptyState>
          </div>
        ) : null}
        <ul className="divide-y divide-line">
          {notifications.data?.items.map((item) => {
            const severity = SEVERITY[item.severity] ?? SEVERITY.info;
            const unread = item.read_at === null;
            return (
              <li key={item.id} className={`grid gap-2 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_auto] ${unread ? "bg-accent-soft/40" : ""}`}>
                <div className="min-w-0 space-y-1">
                  <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <StatusBadge tone={severity.tone} label={severity.label} />
                    <span>{EVENT_LABELS[item.event_type] ?? item.event_type}</span>
                    {item.case_title ? <span className="break-words">· {item.case_title}</span> : null}
                    <span>
                      · <Timestamp value={item.created_at} />
                    </span>
                  </p>
                  <p className={`break-words ${unread ? "font-medium text-ink" : "text-ink"}`}>
                    {unread ? <span className="sr-only">Unread: </span> : null}
                    {item.link ? (
                      <Link
                        href={item.link}
                        className="hover:underline"
                        onClick={() => {
                          if (unread) void mutate(`/api/v1/notifications/${item.id}/read`).catch(() => undefined);
                        }}
                      >
                        {item.title}
                      </Link>
                    ) : (
                      item.title
                    )}
                  </p>
                  {item.body ? <p className="max-w-[72ch] text-sm break-words text-muted">{item.body}</p> : null}
                </div>
                <div className="flex items-start sm:justify-end">
                  <Button size="sm" variant="ghost" onClick={() => void act(() => mutate(`/api/v1/notifications/${item.id}/${unread ? "read" : "unread"}`))}>
                    {unread ? "Mark read" : "Mark unread"}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
        {notifications.data ? (
          <div className="border-t border-line px-4 py-2">
            <Pagination total={notifications.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Panel>
      <p className="max-w-[72ch] text-sm text-muted">
        Notifications name the case and count what changed; they never include collected values, evidence or AI text. If you lose access to a case, its
        notifications disappear from this list. Choose what each monitor reports on its configuration.
      </p>
    </div>
  );
}
