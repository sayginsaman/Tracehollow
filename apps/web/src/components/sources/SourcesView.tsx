"use client";

import { useState, type FormEvent } from "react";

import { COLLECTION_MODE_TEXT, VERIFICATION_TEXT, collectionMode, formatQuota } from "@/lib/connectors";
import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ConnectorDescriptor, CredentialStatus } from "@/lib/workspace-types";

import { CapabilityMatrix } from "./CapabilityMatrix";
import { Button, ErrorNotice, Field, KeyValue, LoadingState, Notice, OutcomeBadge, Section, SyntheticBadge, TextInput, humanize } from "../ui";

function VerificationBadge({ status }: { status: ConnectorDescriptor["verification_status"] }) {
  const tone = status === "live_verified" ? "border-ok/40 bg-ok-bg text-ok" : "border-warn/40 bg-warn-bg text-warn";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${tone}`}>{VERIFICATION_TEXT[status]}</span>
  );
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

  const state = !credential.configured
    ? credential.required
      ? "Not configured (required)"
      : "Not configured (optional)"
    : !credential.usable
      ? "Stored with a different encryption key; set it again"
      : credential.last_result === "rejected"
        ? "Rejected by the source on last use; replace it"
        : "Configured";
  return (
    <li className="rounded-md border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{credential.label}</span>
        <span className={credential.configured && credential.usable && credential.last_result !== "rejected" ? "text-ok" : "text-muted"}>{state}</span>
      </div>
      <p className="mt-1 text-xs text-muted">{credential.description}</p>
      {credential.configured ? (
        <p className="mt-1 text-xs text-muted">
          Updated {formatUtc(credential.updated_at)} · last used {formatUtc(credential.last_used_at)}
          {credential.last_result ? ` (${humanize(credential.last_result)})` : ""}
        </p>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-sm text-bad">{error}</p> : null}
      {admin ? (
        editing ? (
          <form onSubmit={save} className="mt-2 flex flex-wrap items-end gap-2">
            <Field label={`New value for ${credential.label}`} htmlFor={inputId} hint="Stored encrypted; it is never shown again.">
              <TextInput id={inputId} name="value" type="password" autoComplete="off" required maxLength={4096} />
            </Field>
            <Button type="submit" variant="primary" disabled={busy}>
              Save credential
            </Button>
            <Button onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="mt-2 flex gap-2">
            <Button onClick={() => setEditing(true)}>{credential.configured ? "Replace" : "Set"} credential</Button>
            {credential.configured ? (
              <Button variant="danger" onClick={() => void remove()} disabled={busy}>
                Remove
              </Button>
            ) : null}
          </div>
        )
      ) : (
        <p className="mt-2 text-xs text-muted">Only an administrator can change credentials.</p>
      )}
    </li>
  );
}

function ConnectorCard({ connector: initial, admin }: { connector: ConnectorDescriptor; admin: boolean }) {
  const [connector, setConnector] = useState(initial);
  const mode = collectionMode(connector.collection_mode);
  const outcomes = Object.entries(connector.health.recent_outcomes);
  return (
    <Section
      title={connector.display_name}
      description={connector.description}
      actions={
        <span className="flex flex-wrap items-center gap-2">
          {connector.synthetic ? <SyntheticBadge /> : <VerificationBadge status={connector.verification_status} />}
        </span>
      }
    >
      <div className="space-y-4">
        <div className="rounded-md border border-line bg-canvas px-3 py-2 text-sm">
          <span className="font-medium">{mode.label}.</span> <span className="text-muted">{mode.explanation}</span>
        </div>
        <KeyValue
          items={[
            ["Identifier", <span key="id" className="font-mono text-xs">{`${connector.connector_id} ${connector.version}`}</span>],
            ["Inputs", connector.supported_input_types.map(humanize).join(", ")],
            ["Coverage", connector.coverage],
            ["Credentials", connector.credential_requirements],
            ["Cost", connector.cost_model ?? "—"],
            ["Quota", connector.quota_notes ?? "—"],
            ["Limits", `Up to ${connector.max_pages} page(s), ${connector.timeout_seconds}s per run, ${connector.max_concurrent_runs} concurrent run(s)${connector.min_request_interval_seconds ? `, ${connector.min_request_interval_seconds}s between requests to one host` : ""}`],
            ["Retries", connector.retry_max_attempts > 1 ? `${connector.retry_max_attempts} attempts for ${connector.retryable_outcomes.map(humanize).join(", ")}` : "None"],
            ["Caching", connector.cache_policy],
            ["Terms", connector.provider_terms ?? "—"],
            ["Live verification", connector.last_live_verification ?? "Not performed"],
            ["Documentation", connector.documentation ? <span key="doc" className="font-mono text-xs">{connector.documentation}</span> : "—"],
          ]}
        />
        {connector.capabilities && connector.capabilities.length > 0 ? <CapabilityMatrix capabilities={connector.capabilities} /> : null}
        {connector.credentials.length > 0 ? (
          <div>
            <h3 className="mb-2 text-sm font-semibold">Credentials</h3>
            <ul className="space-y-2">
              {connector.credentials.map((credential) => (
                <CredentialRow key={credential.name} connector={connector} credential={credential} admin={admin} onChanged={setConnector} />
              ))}
            </ul>
          </div>
        ) : null}
        <div>
          <h3 className="mb-2 text-sm font-semibold">Recent health in your cases (30 days)</h3>
          {connector.health.last_run_at ? (
            <div className="space-y-1 text-sm">
              <p>
                Last run finished {formatUtc(connector.health.last_run_at)} <OutcomeBadge outcome={connector.health.last_outcome} />
                {connector.health.last_error_code ? <span className="text-xs text-muted"> ({humanize(connector.health.last_error_code)})</span> : null}
              </p>
              {outcomes.length > 0 ? (
                <p className="text-xs text-muted">{outcomes.map(([outcome, count]) => `${humanize(outcome)}: ${count}`).join(" · ")}</p>
              ) : null}
              {connector.health.last_quota ? <p className="text-xs text-muted">Quota: {formatQuota(connector.health.last_quota)}</p> : null}
            </div>
          ) : (
            <p className="text-sm text-muted">No runs yet in cases you can open.</p>
          )}
        </div>
      </div>
    </Section>
  );
}

export function SourcesView() {
  const { session } = useSession();
  const connectors = useResource<ConnectorDescriptor[]>("/api/v1/connectors");
  if (connectors.state === "error" && !connectors.data) return <ErrorNotice error={connectors.error} onRetry={() => void connectors.reload()} />;
  if (!connectors.data) return <LoadingState label="Loading sources…" />;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Sources</h1>
        <p className="mt-1 text-sm text-muted">
          What each collector can reach, how it reaches it, what it needs and how it has behaved. Choose sources in a case under
          Queries &amp; runs.
        </p>
      </div>
      <Notice tone="warn">
        No connector here has been verified against its live source yet; results come from fixture-based contract tests.
        Collection mode tells you who sees each request: {Object.values(COLLECTION_MODE_TEXT).filter((m) => m.label !== "Synthetic fixture").map((m) => m.label).join(", ")}.
      </Notice>
      {connectors.data.map((connector) => (
        <ConnectorCard key={connector.connector_id} connector={connector} admin={session.user.is_admin} />
      ))}
    </div>
  );
}
