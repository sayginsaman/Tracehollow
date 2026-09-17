"use client";

import { useState } from "react";

import { hasSystemPermission } from "@/lib/permissions";
import { useResource, useSession } from "@/lib/session-context";
import type { DirectoryCase, Page } from "@/lib/workspace-types";

import { MembersManager } from "../team/MembersManager";
import {
  Button,
  ChoiceField,
  DataTable,
  EmptyState,
  ErrorNotice,
  LoadingState,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  StatusBadge,
  Td,
  Th,
  Timestamp,
  Tr,
} from "../ui";
import { AdminGate } from "./AdminGate";

const PAGE = 50;

function Directory({ startWithoutAnalyst }: { startWithoutAnalyst: boolean }) {
  const { session } = useSession();
  const [withoutAnalyst, setWithoutAnalyst] = useState(startWithoutAnalyst);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<DirectoryCase | null>(null);
  const cases = useResource<Page<DirectoryCase>>(`/api/v1/admin/cases?limit=${PAGE}&offset=${offset}${withoutAnalyst ? "&without_active_analyst=true" : ""}`);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Case access"
        description="Repair who can open a case, for example when its last analyst left. This directory shows titles and member counts only; investigation content stays visible to members alone."
      />
      <Notice tone="neutral">
        Adding yourself to a case gives you access to its content and is recorded in the audit log. Administrators do not see case content otherwise.
      </Notice>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Cases" flush>
          <div className="border-b border-line px-4 py-3">
            <ChoiceField
              label="Only cases without an active analyst"
              checked={withoutAnalyst}
              onChange={(event) => {
                setWithoutAnalyst(event.target.checked);
                setOffset(0);
              }}
            />
          </div>
          {cases.state === "error" ? (
            <div className="p-4">
              <ErrorNotice error={cases.error} onRetry={() => void cases.reload()} />
            </div>
          ) : null}
          {!cases.data && cases.state === "loading" ? <LoadingState className="p-4" label="Loading cases…" /> : null}
          {cases.data && cases.data.items.length === 0 ? (
            <div className="p-4">
              <EmptyState compact>{withoutAnalyst ? "Every case has at least one active analyst." : "No cases."}</EmptyState>
            </div>
          ) : null}
          {cases.data?.items.length ? (
            <DataTable caption="Case directory" minWidth="34rem">
              <thead>
                <tr>
                  <Th>Case</Th>
                  <Th>Members</Th>
                  <Th>Active analysts</Th>
                  <Th className="text-right">Access</Th>
                </tr>
              </thead>
              <tbody>
                {cases.data.items.map((item) => (
                  <Tr key={item.id} className={selected?.id === item.id ? "bg-accent-soft" : undefined}>
                    <Td>
                      <span className="font-medium break-words text-ink">{item.title}</span>
                      <span className="block text-xs text-muted">
                        {item.status === "archived" ? "Archived · " : ""}Created <Timestamp value={item.created_at} />
                      </span>
                    </Td>
                    <Td className="text-sm">{item.member_count}</Td>
                    <Td>{item.active_analyst_count ? <span className="text-sm">{item.active_analyst_count}</span> : <StatusBadge tone="warn" label="None" />}</Td>
                    <Td className="text-right">
                      <Button size="sm" aria-pressed={selected?.id === item.id} onClick={() => setSelected(item)}>
                        Manage members
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          ) : null}
          {cases.data ? (
            <div className="border-t border-line px-4 py-2">
              <Pagination total={cases.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
            </div>
          ) : null}
        </Panel>

        {selected ? (
          <Panel title={`Members of ${selected.title}`} description="Changes apply immediately and stop queued work of anyone who loses analyst access.">
            <MembersManager
              key={selected.id}
              membersPath={`/api/v1/admin/cases/${selected.id}/members`}
              canManage
              accountSearch={hasSystemPermission(session, "accounts.search")}
              currentUserId={session.user.id}
              onChanged={() => void cases.reload()}
            />
          </Panel>
        ) : (
          <Panel title="Members">
            <p className="text-sm text-muted">Choose a case to see and change its members.</p>
          </Panel>
        )}
      </div>
    </div>
  );
}

export function CaseAccessView({ withoutAnalyst = false }: { withoutAnalyst?: boolean }) {
  return (
    <AdminGate permission="case_access.manage">
      <Directory startWithoutAnalyst={withoutAnalyst} />
    </AdminGate>
  );
}
