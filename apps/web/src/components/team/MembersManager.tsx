"use client";

import { UserPlus } from "lucide-react";
import { useId, useState } from "react";

import { describeError } from "@/lib/messages";
import { ROLE_LABELS } from "@/lib/permissions";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseRole, DirectoryAccount, Member } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  ConfirmAction,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  LoadingState,
  RoleBadge,
  Select,
  Td,
  TextInput,
  Th,
  Timestamp,
  Tr,
} from "../ui";

function useAccountSuggestions(query: string, enabled: boolean): DirectoryAccount[] {
  const term = query.trim();
  const accounts = useResource<DirectoryAccount[]>(enabled && term.length >= 1 ? `/api/v1/accounts?q=${encodeURIComponent(term)}&limit=10` : null);
  return accounts.data ?? [];
}

/**
 * Case membership list with add, role change and removal. Used by the case Members page (analysts)
 * and by administrators on the Case access page; the API enforces the last-analyst rule either way.
 */
export function MembersManager({
  membersPath,
  canManage,
  accountSearch,
  currentUserId,
  onChanged,
}: {
  membersPath: string;
  canManage: boolean;
  accountSearch: boolean;
  currentUserId: string;
  onChanged?: () => void;
}) {
  const { mutate } = useSession();
  const members = useResource<Member[]>(membersPath);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<CaseRole>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const suggestions = useAccountSuggestions(username, canManage && accountSearch);
  const listId = useId();
  const chosen = suggestions.find((account) => account.username.toLowerCase() === username.trim().toLowerCase());

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
      await members.reload();
      onChanged?.();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  const analysts = (members.data ?? []).filter((member) => member.effective_role === "analyst" && member.account_active).length;

  return (
    <div className="space-y-4">
      <ActionError message={error} />
      {members.state === "error" && !members.data ? <ErrorNotice error={members.error} onRetry={() => void members.reload()} /> : null}
      {!members.data && members.state === "loading" ? <LoadingState label="Loading members…" /> : null}
      {members.data && members.data.length === 0 ? <EmptyState compact>No members. Add an analyst so someone can work on this case.</EmptyState> : null}
      {members.data?.length ? (
        <DataTable caption="Case members" minWidth="44rem">
          <thead>
            <tr>
              <Th>Account</Th>
              <Th>Role in this case</Th>
              <Th>Effective access</Th>
              <Th>Added</Th>
              {canManage ? <Th className="text-right">Actions</Th> : null}
            </tr>
          </thead>
          <tbody>
            {members.data.map((member) => {
              const lastAnalyst = member.effective_role === "analyst" && member.account_active && analysts <= 1;
              const limited = member.account_role === "viewer";
              return (
                <Tr key={member.user_id}>
                  <Td>
                    <span className="font-medium text-ink">{member.username}</span>
                    {member.user_id === currentUserId ? <span className="ml-1.5 text-xs text-muted">(you)</span> : null}
                    <span className="mt-0.5 block text-xs text-muted">
                      {ROLE_LABELS[member.account_role]} account{member.account_active ? "" : ", deactivated"}
                    </span>
                  </Td>
                  <Td>
                    {canManage ? (
                      <Select
                        aria-label={`Role of ${member.username}`}
                        value={member.membership_role}
                        disabled={busy !== null || (lastAnalyst && member.membership_role === "analyst")}
                        onChange={(event) =>
                          void run(`role-${member.user_id}`, () =>
                            mutate(`${membersPath}/${member.user_id}`, { method: "PATCH", body: { role: event.target.value } }),
                          )
                        }
                        className="w-32"
                      >
                        <option value="analyst" disabled={limited}>
                          Analyst
                        </option>
                        <option value="viewer">Viewer</option>
                      </Select>
                    ) : (
                      <RoleBadge role={member.membership_role} />
                    )}
                  </Td>
                  <Td>
                    <RoleBadge role={member.account_active ? member.effective_role : "none"} />
                    {member.effective_role !== member.membership_role ? (
                      <span className="mt-0.5 block text-xs text-muted">Limited by the account role</span>
                    ) : null}
                  </Td>
                  <Td className="whitespace-nowrap text-sm">
                    <Timestamp value={member.added_at} />
                  </Td>
                  {canManage ? (
                    <Td className="text-right">
                      {lastAnalyst ? (
                        <span className="text-xs text-muted">Last analyst</span>
                      ) : (
                        <ConfirmAction
                          label="Remove"
                          confirmLabel="Remove member"
                          message={`Remove ${member.username}? Their queued work stops.`}
                          busy={busy === `remove-${member.user_id}`}
                          onConfirm={() => void run(`remove-${member.user_id}`, () => mutate(`${membersPath}/${member.user_id}`, { method: "DELETE" }))}
                        />
                      )}
                    </Td>
                  ) : null}
                </Tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : null}

      {canManage ? (
        <form
          className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            if (!username.trim()) return;
            void run("add", async () => {
              await mutate(membersPath, { body: { username: username.trim(), role } });
              setUsername("");
            });
          }}
        >
          <Field
            label="Add an account"
            htmlFor={`${listId}-username`}
            hint={chosen ? `${ROLE_LABELS[chosen.role]} account` : "Enter an existing local account's username. Accounts are created by an administrator."}
          >
            <TextInput
              id={`${listId}-username`}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              list={accountSearch ? `${listId}-accounts` : undefined}
              autoComplete="off"
              maxLength={64}
            />
          </Field>
          {accountSearch ? (
            <datalist id={`${listId}-accounts`}>
              {suggestions.map((account) => (
                <option key={account.id} value={account.username}>
                  {ROLE_LABELS[account.role]}
                </option>
              ))}
            </datalist>
          ) : null}
          <Field label="Role" htmlFor={`${listId}-role`}>
            <Select id={`${listId}-role`} value={role} onChange={(event) => setRole(event.target.value as CaseRole)}>
              <option value="viewer">Viewer</option>
              <option value="analyst" disabled={chosen?.role === "viewer"}>
                Analyst
              </option>
            </Select>
          </Field>
          <Button type="submit" icon={UserPlus} busy={busy === "add"} disabled={busy !== null || !username.trim()}>
            Add member
          </Button>
        </form>
      ) : null}
    </div>
  );
}
