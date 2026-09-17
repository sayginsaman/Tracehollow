"use client";

import { KeyRound, Plug } from "lucide-react";
import { useState, type FormEvent } from "react";

import { COLLECTION_MODE_TEXT, collectionMode, formatQuota } from "@/lib/connectors";
import { describeError } from "@/lib/messages";
import { hasSystemPermission } from "@/lib/permissions";
import { useResource, useSession } from "@/lib/session-context";
import type { ConnectorDescriptor, CredentialStatus } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  ConfirmAction,
  Disclosure,
  ErrorNotice,
  Field,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  OutcomeBadge,
  PageHeader,
  SegmentedFilter,
  StatusBadge,
  SubHeading,
  SyntheticBadge,
  TextInput,
  Timestamp,
  VerificationBadge,
  humanize,
} from "../ui";
import { CapabilityMatrix } from "./CapabilityMatrix";

type SourceFilter = "all" | "credentials" | "platforms" | "web";

function credentialState(credential: CredentialStatus): { tone: "ok" | "warn" | "bad" | "neutral"; text: string } {
  if (!credential.configured) return credential.required ? { tone: "warn", text: "Not configured (required)" } : { tone: "neutral", text: "Not configured (optional)" };
  if (!credential.usable) return { tone: "bad", text: "Stored with a different encryption key; set it again" };
  if (credential.last_result === "rejected") return { tone: "bad", text: "Rejected by the source on last use; replace it" };
  return { tone: "ok", text: "Configured" };
}

function CredentialRow({
  connector,
  credential,
  admin,
  onChanged,
}: {
  connector: ConnectorDescriptor;
  credential: CredentialStatus;
  admin: boolean;
  onChanged: (next: ConnectorDescriptor) => void;
}) {
  const { mutate } = useSession();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = `credential-${connector.connector_id}-${credential.name}`;
  const path = `/api/v1/connectors/${connector.connector_id}/credentials/${credential.name}`;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const value = String(new FormData(form).get("value") ?? "");
    setBusy(true);
    setError(null);
    try {
      const next = await mutate<ConnectorDescriptor>(path, { body: { value } });
      form.reset();
      setEditing(false);
      onChanged(next);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      onChanged(await mutate<ConnectorDescriptor>(path, { method: "DELETE" }));
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const state = credentialState(credential);
  return (
    <li className="space-y-2 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium text-ink">
          <KeyRound aria-hidden="true" className="size-4 text-muted" />
          {credential.label}
        </span>
        <StatusBadge tone={state.tone} label={state.text} />
      </div>
      <p className="text-xs text-muted">{credential.description}</p>
      {credential.configured ? (
        <p className="text-xs text-muted">
          Value hidden. Updated <Timestamp value={credential.updated_at} /> · last used <Timestamp value={credential.last_used_at} fallback="never" />
          {credential.last_result ? ` (${humanize(credential.last_result)})` : ""}
        </p>
      ) : null}
      <ActionError message={error} />
      {admin ? (
        editing ? (
          <form onSubmit={save} className="flex flex-wrap items-end gap-2">
            <Field label={`New value for ${credential.label}`} htmlFor={inputId} hint="Stored encrypted; it is never shown again." className="min-w-56 flex-1">
              <TextInput id={inputId} name="value" type="password" autoComplete="off" required maxLength={4096} />
            </Field>
            <Button type="submit" variant="primary" disabled={busy} busy={busy}>
              Save credential
            </Button>
            <Button onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setEditing(true)}>
              {credential.configured ? "Replace" : "Set"} credential
            </Button>
            {credential.configured ? (
              <ConfirmAction label="Remove" confirmLabel="Remove" message="Remove this stored value?" busy={busy} onConfirm={() => void remove()} />
            ) : null}
          </div>
        )
      ) : (
        <p className="text-xs text-muted">Only an administrator can change credentials.</p>
      )}
    </li>
  );
}

function ConnectorCard({ connector: initial, admin }: { connector: ConnectorDescriptor; admin: boolean }) {
  const [connector, setConnector] = useState(initial);
  const mode = collectionMode(connector.collection_mode);
  const outcomes = Object.entries(connector.health.recent_outcomes);
  const headingId = `source-${connector.connector_id}`;
  return (
    <section aria-labelledby={headingId} className="scroll-mt-20 rounded-lg border border-line bg-surface" id={connector.connector_id}>
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1 basis-72">
          <h2 id={headingId} className="text-heading font-semibold text-ink">
            {connector.display_name}
          </h2>
          <p className="mt-0.5 max-w-[72ch] text-sm text-muted">{connector.description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {connector.synthetic ? <SyntheticBadge /> : <VerificationBadge status={connector.verification_status} />}
        </div>
      </header>
      <div className="grid gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <div className="min-w-0 space-y-4">
          <p className="flex items-start gap-2 rounded-md bg-sunken px-3 py-2 text-sm">
            <Plug aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
            <span>
              <span className="font-medium text-ink">{mode.label}.</span> <span className="text-muted">{mode.explanation}</span>
            </span>
          </p>
          <KeyValue
            compact
            items={[
              ["Inputs", connector.supported_input_types.map(humanize).join(", ")],
              ["Credentials", connector.credential_requirements],
              ["Cost", connector.cost_model ?? "Not stated"],
              [
                "Limits",
                `Up to ${connector.max_pages} page(s), ${connector.timeout_seconds}s per run, ${connector.max_concurrent_runs} concurrent run(s)${connector.min_request_interval_seconds ? `, ${connector.min_request_interval_seconds}s between requests to one host` : ""}`,
              ],
            ]}
          />
          {connector.capabilities && connector.capabilities.length > 0 ? <CapabilityMatrix capabilities={connector.capabilities} /> : null}
          <Disclosure summary="Full specification">
            <KeyValue
              compact
              items={[
                ["Identifier", <Mono key="id">{`${connector.connector_id} ${connector.version}`}</Mono>],
                ["Coverage", connector.coverage],
                ["Quota", connector.quota_notes ?? "Not stated"],
                ["Retries", connector.retry_max_attempts > 1 ? `${connector.retry_max_attempts} attempts for ${connector.retryable_outcomes.map(humanize).join(", ")}` : "None"],
                ["Caching", connector.cache_policy],
                ["Terms", connector.provider_terms ?? "Not stated"],
                ["Live verification", connector.last_live_verification ?? "Not performed"],
                ["Documentation", connector.documentation ? <Mono key="doc">{connector.documentation}</Mono> : "None"],
              ]}
            />
          </Disclosure>
        </div>
        <div className="min-w-0 space-y-4">
          {connector.credentials.length > 0 ? (
            <div className="space-y-2">
              <SubHeading>Credentials</SubHeading>
              <ul className="divide-y divide-line rounded-md border border-line">
                {connector.credentials.map((credential) => (
                  <CredentialRow key={credential.name} connector={connector} credential={credential} admin={admin} onChanged={setConnector} />
                ))}
              </ul>
            </div>
          ) : null}
          <div className="space-y-2">
            <SubHeading>Recent health in your cases (30 days)</SubHeading>
            {connector.health.last_run_at ? (
              <div className="space-y-1.5 text-sm">
                <p className="flex flex-wrap items-center gap-2 text-ink">
                  Last run finished <Timestamp value={connector.health.last_run_at} /> <OutcomeBadge outcome={connector.health.last_outcome} />
                </p>
                {connector.health.last_error_code ? <p className="text-xs text-muted">Last error: {humanize(connector.health.last_error_code)}</p> : null}
                {outcomes.length > 0 ? <p className="text-xs text-muted">{outcomes.map(([outcome, count]) => `${humanize(outcome)}: ${count}`).join(" · ")}</p> : null}
                {connector.health.last_quota ? <p className="text-xs text-muted">Quota: {formatQuota(connector.health.last_quota)}</p> : null}
              </div>
            ) : (
              <p className="text-sm text-muted">No runs yet in cases you can open.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function matches(connector: ConnectorDescriptor, filter: SourceFilter): boolean {
  if (filter === "credentials") return connector.credentials.length > 0;
  if (filter === "platforms") return Boolean(connector.capabilities?.length) || connector.collection_mode === "platform_probe";
  if (filter === "web") return connector.collection_mode === "direct_request";
  return true;
}

export function SourcesView() {
  const { session } = useSession();
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  const [filter, setFilter] = useState<SourceFilter>("all");

  const header = (
    <PageHeader
      title="Sources"
      description="What each collector can reach, how it reaches it, what it needs and how it has behaved. Choose sources in a case under Queries & runs."
    />
  );
  if (connectors.state === "error" && !connectors.data) {
    return (
      <div className="space-y-6">
        {header}
        <ErrorNotice error={connectors.error} onRetry={() => void connectors.reload()} />
      </div>
    );
  }
  if (!connectors.data) {
    return (
      <div className="space-y-6">
        {header}
        <LoadingState label="Loading sources…" rows={6} />
      </div>
    );
  }
  const live = connectors.data.filter((connector) => connector.verification_status === "live_verified").length;
  const shown = connectors.data.filter((connector) => matches(connector, filter));
  return (
    <div className="space-y-6">
      {header}
      <Notice tone="warn">
        {live === 0
          ? "No connector here has been verified against its live source yet; results come from fixture-based contract tests."
          : `${live} connector(s) have been verified against their live source; the others come from fixture-based contract tests only.`}{" "}
        Collection mode tells you who sees each request:{" "}
        {Object.values(COLLECTION_MODE_TEXT)
          .filter((mode) => mode.label !== "Synthetic fixture")
          .map((mode) => mode.label)
          .join(", ")}
        .
      </Notice>
      {connectors.data.length > 4 ? (
        <SegmentedFilter
          label="Show sources"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: `All (${connectors.data.length})` },
            { value: "credentials", label: "Use credentials" },
            { value: "platforms", label: "Social and platform" },
            { value: "web", label: "Direct web requests" },
          ]}
        />
      ) : null}
      <div className="space-y-6">
        {shown.map((connector) => (
          <ConnectorCard key={connector.connector_id} connector={connector} admin={hasSystemPermission(session, "credentials.manage")} />
        ))}
      </div>
    </div>
  );
}
