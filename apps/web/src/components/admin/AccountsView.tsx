"use client";

import { KeyRound, Plus, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { describeError } from "@/lib/messages";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/lib/permissions";
import { useResource, useSession } from "@/lib/session-context";
import type { Account, AccountChange, Page } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  DataTable,
  ErrorNotice,
  Field,
  IconButton,
  KeyValue,
  LoadingState,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  Select,
  StatusBadge,
  Td,
  TextInput,
  Th,
  Timestamp,
  Tr,
} from "../ui";
import { AdminGate } from "./AdminGate";

const PAGE = 50;
type Role = Account["role"];

function CreateAccount({ onCreated, onClose }: { onCreated: () => Promise<void>; onClose: () => void }) {
  const { mutate } = useSession();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="New account" description="A local account signs in with a username and password. Share the initial password privately; there are no email invitations." actions={<IconButton icon={X} label="Close form" onClick={onClose} />}>
      <form
        className="grid items-end gap-3 md:grid-cols-[minmax(0,1fr)_10rem_minmax(0,1fr)_auto]"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await mutate("/api/v1/admin/accounts", { body: { username, role, password } });
            setUsername("");
            setPassword("");
            await onCreated();
          } catch (caught) {
            setError(describeError(caught));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Username" htmlFor="account-username">
          <TextInput id="account-username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" required maxLength={64} />
        </Field>
        <Field label="Account role" htmlFor="account-role">
          <Select id="account-role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
            <option value="analyst">Analyst</option>
            <option value="viewer">Viewer</option>
            <option value="administrator">Administrator</option>
          </Select>
        </Field>
        <Field label="Initial password" htmlFor="account-password" hint="At least 12 characters.">
          <TextInput id="account-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required />
        </Field>
        <Button type="submit" variant="primary" busy={busy} disabled={busy || !username || !password}>
          Create account
        </Button>
        <ActionError message={error} className="md:col-span-4" />
      </form>
    </Panel>
  );
}

function PasswordReset({ account, onDone }: { account: Account; onDone: () => void }) {
  const { mutate } = useSession();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="mt-2 flex flex-wrap items-end gap-2"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await mutate(`/api/v1/admin/accounts/${account.id}/password`, { body: { password } });
          onDone();
        } catch (caught) {
          setError(describeError(caught));
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field label={`New password for ${account.username}`} htmlFor={`reset-${account.id}`} hint="Signs the account out everywhere.">
        <TextInput id={`reset-${account.id}`} type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required />
      </Field>
      <Button type="submit" size="sm" busy={busy} disabled={busy || !password}>
        Set password
      </Button>
      <Button size="sm" variant="ghost" onClick={onDone}>
        Cancel
      </Button>
      <ActionError message={error} className="basis-full" />
    </form>
  );
}

function AccountsTable() {
  const { mutate, session } = useSession();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [orphaned, setOrphaned] = useState<string[]>([]);
  const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (search.trim()) query.set("q", search.trim());
  const accounts = useResource<Page<Account>>(`/api/v1/admin/accounts?${query.toString()}`);

  async function change(account: Account, body: { role?: Role; is_active?: boolean }) {
    setError(null);
    setNotice(null);
    try {
      const result = await mutate<AccountChange>(`/api/v1/admin/accounts/${account.id}`, { method: "PATCH", body });
      setOrphaned(result.cases_without_active_analyst);
      await accounts.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Accounts"
        description="Local accounts and their account role. The account role is a ceiling: it limits what a person can do in any case, but opening a case still requires membership."
        actions={
          !creating ? (
            <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>
              New account
            </Button>
          ) : undefined
        }
      />
      {creating ? (
        <CreateAccount
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false);
            setNotice("Account created. It has no case access until someone adds it to a case.");
            await accounts.reload();
          }}
        />
      ) : null}
      <ActionError message={error} />
      {notice ? <Notice tone="ok">{notice}</Notice> : null}
      {orphaned.length ? (
        <Notice
          tone="warn"
          title={`${orphaned.length} case${orphaned.length === 1 ? " has" : "s have"} no active analyst now`}
          actions={
            <Link href="/admin/case-access?without_analyst=1" className="text-sm font-medium text-accent hover:underline">
              Review case access
            </Link>
          }
        >
          Nobody can change, collect or manage members in those cases until an analyst is added.
        </Notice>
      ) : null}

      <Panel title="All accounts" flush>
        <div className="border-b border-line px-4 py-3">
          <Field label="Search usernames" htmlFor="account-search" className="max-w-xs">
            <TextInput
              id="account-search"
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setOffset(0);
              }}
            />
          </Field>
        </div>
        {accounts.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={accounts.error} onRetry={() => void accounts.reload()} />
          </div>
        ) : null}
        {!accounts.data && accounts.state === "loading" ? <LoadingState className="p-4" label="Loading accounts…" /> : null}
        {accounts.data ? (
          <DataTable caption="Accounts" minWidth="56rem">
            <thead>
              <tr>
                <Th>Account</Th>
                <Th>Account role</Th>
                <Th>Status</Th>
                <Th>Case memberships</Th>
                <Th>Last sign-in</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {accounts.data.items.map((account) => {
                const self = account.id === session.user.id;
                return (
                  <Tr key={account.id}>
                    <Td>
                      <span className="font-medium text-ink">{account.username}</span>
                      {self ? <span className="ml-1.5 text-xs text-muted">(you)</span> : null}
                      <span className="block text-xs text-muted">
                        Created <Timestamp value={account.created_at} />
                      </span>
                      {resetting === account.id ? <PasswordReset account={account} onDone={() => setResetting(null)} /> : null}
                    </Td>
                    <Td>
                      <Select aria-label={`Account role of ${account.username}`} value={account.role} onChange={(event) => void change(account, { role: event.target.value as Role })} className="w-40">
                        <option value="administrator">Administrator</option>
                        <option value="analyst">Analyst</option>
                        <option value="viewer">Viewer</option>
                      </Select>
                    </Td>
                    <Td>
                      {account.is_active ? <StatusBadge tone="ok" label="Active" /> : <StatusBadge tone="neutral" label="Deactivated" />}
                      {account.locked_until && new Date(account.locked_until) > new Date() ? <span className="mt-1 block text-xs text-warn">Sign-in temporarily locked</span> : null}
                    </Td>
                    <Td className="text-sm">
                      {account.case_count} ({account.analyst_case_count} as analyst)
                    </Td>
                    <Td className="text-sm whitespace-nowrap">
                      <Timestamp value={account.last_login_at} fallback="Never" />
                    </Td>
                    <Td className="text-right">
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button size="sm" variant="ghost" icon={KeyRound} onClick={() => setResetting(account.id)}>
                          Reset password
                        </Button>
                        {account.is_active ? (
                          <Button size="sm" variant="danger-ghost" disabled={self} onClick={() => void change(account, { is_active: false })}>
                            Deactivate
                          </Button>
                        ) : (
                          <Button size="sm" onClick={() => void change(account, { is_active: true })}>
                            Reactivate
                          </Button>
                        )}
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </DataTable>
        ) : null}
        {accounts.data ? (
          <div className="border-t border-line px-4 py-2">
            <Pagination total={accounts.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Panel>

      <Panel title="Account roles">
        <KeyValue items={(["administrator", "analyst", "viewer"] as const).map((key) => [ROLE_LABELS[key] ?? key, ROLE_DESCRIPTIONS[key]])} />
        <p className="mt-3 max-w-[72ch] text-sm text-muted">
          Deactivating an account signs it out, stops its queued collection and AI work, and keeps its history. The last active administrator cannot be demoted or
          deactivated.
        </p>
      </Panel>
    </div>
  );
}

export function AccountsView() {
  return (
    <AdminGate permission="accounts.manage">
      <AccountsTable />
    </AdminGate>
  );
}
