"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { EntityDetail, Evidence, Observation, Page, Vocabulary } from "@/lib/workspace-types";

import {
  Button,
  EmptyState,
  ErrorNotice,
  KeyValue,
  LoadingState,
  Mono,
  OriginBadge,
  Section,
  Select,
  SyntheticBadge,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";
import { NotesPanel } from "./NotesPanel";
import { RelationshipTable } from "./RelationshipTable";

export function EntityDetailView({ entityId }: { entityId: string }) {
  const { apiBase, base, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const detail = useResource<EntityDetail>(`${apiBase}/entities/${entityId}`);
  const vocabulary = useResource<Vocabulary>(`${apiBase}/entity-vocabulary`);
  const observations = useResource<Page<Observation>>(`${apiBase}/observations?entity_id=${entityId}&limit=20`);
  const evidenceOptions = useResource<Page<Evidence>>(writable ? `${apiBase}/evidence?limit=100` : null);
  const [error, setError] = useState<string | null>(null);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  if (!detail.data) return <LoadingState label="Loading entity…" />;
  const { entity } = detail.data;
  const analystOwned = entity.origin === "analyst_assertion";

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await detail.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  async function addIdentifier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const platform = String(form.get("platform") ?? "").trim();
    await run(async () => {
      await mutate(`${apiBase}/entities/${entityId}/identifiers`, {
        body: {
          identifier_type: String(form.get("identifier_type")),
          value: String(form.get("value") ?? ""),
          ...(platform ? { platform } : {}),
        },
      });
      formElement.reset();
    });
  }

  async function linkEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await run(() =>
      mutate(`${apiBase}/entities/${entityId}/evidence-links`, {
        body: { evidence_id: String(form.get("evidence_id")), note: String(form.get("note") ?? "") || null },
      }),
    );
  }

  async function deleteEntity() {
    setError(null);
    try {
      await mutate(`${apiBase}/entities/${entityId}`, { method: "DELETE" });
      await refreshCase();
      router.push(`${base}/entities`);
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`${base}/entities`} className="text-sm text-accent hover:underline">
          ← Entities
        </Link>
        <h2 className="text-xl font-semibold">{entity.display_name}</h2>
        <OriginBadge origin={entity.origin} />
        {entity.attributes.synthetic ? <SyntheticBadge /> : null}
      </div>
      {error ? <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">{error}</p> : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section
          title="Details"
          actions={
            writable && analystOwned ? (
              <Button variant="danger" onClick={() => void deleteEntity()}>
                Delete entity
              </Button>
            ) : null
          }
        >
          <KeyValue
            items={[
              ["Type", humanize(entity.entity_type)],
              ["Description", entity.description || "—"],
              ["Created", formatUtc(entity.created_at)],
              [
                "Created by run",
                entity.created_by_query_run_id ? (
                  <Link href={`${base}/runs/${entity.created_by_query_run_id}`} className="text-accent hover:underline">
                    View execution
                  </Link>
                ) : (
                  "—"
                ),
              ],
              ["Observations", String(detail.data.observation_count)],
            ]}
          />
          {entity.attributes.candidate ? (
            <p className="mt-3 text-xs text-muted">
              Candidate account: a connector reported it; it is not evidence that any person controls it.
            </p>
          ) : null}
        </Section>

        <Section title="Identifiers" description="Original values are preserved; normalized values are comparison keys only.">
          {entity.identifiers.length === 0 ? <EmptyState>No identifiers.</EmptyState> : null}
          <ul className="space-y-2 text-sm">
            {entity.identifiers.map((identifier) => (
              <li key={identifier.id} className="rounded-md border border-line p-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs text-muted">
                    {humanize(identifier.identifier_type)}
                    {identifier.platform ? ` · ${identifier.platform}` : ""}
                  </span>
                  {writable && analystOwned ? (
                    <button
                      type="button"
                      className="text-xs text-bad hover:underline"
                      onClick={() => void run(() => mutate(`${apiBase}/entities/${entityId}/identifiers/${identifier.id}`, { method: "DELETE" }))}
                    >
                      Remove
                    </button>
                  ) : null}
                </div>
                <div>
                  Original: <Mono>{identifier.original_value}</Mono>
                </div>
                <div className="text-muted">
                  Normalized: <Mono>{identifier.normalized_value}</Mono>
                </div>
              </li>
            ))}
          </ul>
          {writable ? (
            <form onSubmit={addIdentifier} className="mt-3 grid gap-2 md:grid-cols-[9rem_1fr_9rem_auto]">
              <label className="sr-only" htmlFor="new-identifier-type">Identifier type</label>
              <select id="new-identifier-type" name="identifier_type" className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm">
                {(vocabulary.data?.identifier_types ?? []).map((type) => (
                  <option key={type} value={type}>
                    {humanize(type)}
                  </option>
                ))}
              </select>
              <label className="sr-only" htmlFor="new-identifier-value">Value</label>
              <input id="new-identifier-value" name="value" required placeholder="Value" className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm" />
              <label className="sr-only" htmlFor="new-identifier-platform">Platform</label>
              <input id="new-identifier-platform" name="platform" placeholder="Platform" className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm" />
              <Button type="submit">Add</Button>
            </form>
          ) : null}
        </Section>
      </div>

      {detail.data.shared_identifiers.length > 0 ? (
        <Section
          title="Other entities sharing an identifier"
          description="Shown for review only. Tracehollow never merges entities automatically; a shared username or email is not proof of the same person."
        >
          <ul className="space-y-1 text-sm">
            {detail.data.shared_identifiers.map((shared) => (
              <li key={`${shared.entity_id}-${shared.identifier_type}-${shared.normalized_value}`}>
                <Link href={`${base}/entities/${shared.entity_id}`} className="text-accent hover:underline">
                  {shared.display_name}
                </Link>{" "}
                <span className="text-muted">
                  ({humanize(shared.entity_type)}) shares {humanize(shared.identifier_type).toLowerCase()} <Mono>{shared.normalized_value}</Mono>
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Linked evidence">
        {detail.data.linked_evidence.length === 0 ? <EmptyState>No evidence linked yet.</EmptyState> : null}
        <ul className="divide-y divide-line text-sm">
          {detail.data.linked_evidence.map((link) => (
            <li key={link.link_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                <Link href={`${base}/evidence/${link.evidence_id}`} className="text-accent hover:underline">
                  {link.title}
                </Link>{" "}
                {link.acquisition_method === "synthetic_fixture" ? <SyntheticBadge /> : null}
                {link.note ? <span className="block text-xs text-muted">{link.note}</span> : null}
              </span>
              {writable ? (
                <button
                  type="button"
                  className="text-xs text-bad hover:underline"
                  onClick={() => void run(() => mutate(`${apiBase}/entities/${entityId}/evidence-links/${link.link_id}`, { method: "DELETE" }))}
                >
                  Unlink
                </button>
              ) : null}
            </li>
          ))}
        </ul>
        {writable ? (
          <form onSubmit={linkEvidence} className="mt-3 flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1">
              <label htmlFor="link-evidence" className="block text-xs text-muted">
                Evidence in this case
              </label>
              <Select id="link-evidence" name="evidence_id" required>
                {(evidenceOptions.data?.items ?? []).map((evidence) => (
                  <option key={evidence.id} value={evidence.id}>
                    {evidence.title}
                    {evidence.synthetic ? " (synthetic)" : ""}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-w-48 flex-1">
              <label htmlFor="link-note" className="block text-xs text-muted">
                Note (optional)
              </label>
              <input id="link-note" name="note" maxLength={2000} className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-2 text-sm" />
            </div>
            <Button type="submit" disabled={(evidenceOptions.data?.items.length ?? 0) === 0}>
              Link evidence
            </Button>
          </form>
        ) : null}
      </Section>

      <Section title="Relationships">
        <RelationshipTable relationships={detail.data.relationships} onChanged={() => void detail.reload()} />
      </Section>

      <Section title="Observations" description="Source-specific facts that reference this entity, ordered by collection time.">
        {observations.state === "error" ? <ErrorNotice error={observations.error} /> : null}
        {observations.data && observations.data.items.length === 0 ? <EmptyState>No observations reference this entity.</EmptyState> : null}
        <ul className="space-y-2 text-sm">
          {observations.data?.items.map((observation) => (
            <li key={observation.id} className="rounded-md border border-line p-2">
              <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
                <span>{humanize(observation.observation_type)}</span>
                <span>Collected {formatUtc(observation.collected_at)}</span>
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs">
                {JSON.stringify(observation.payload, null, 2)}
              </pre>
              {observation.evidence_id ? (
                <Link href={`${base}/evidence/${observation.evidence_id}`} className="text-xs text-accent hover:underline">
                  Source evidence
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      </Section>

      <NotesPanel subject={{ entity_id: entityId }} title="Notes on this entity" />
    </div>
  );
}
