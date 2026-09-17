"use client";

import { GitCompareArrows, Link2, Plus, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { observationLabel } from "@/lib/connectors";
import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { EntityDetail, Evidence, Observation, Page, Vocabulary } from "@/lib/workspace-types";

import { usePageCrumb } from "../shell/ShellContext";
import {
  ActionError,
  Button,
  ButtonLink,
  ConfirmAction,
  DataTable,
  Disclosure,
  EmptyState,
  ErrorNotice,
  Field,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  OriginBadge,
  PageHeader,
  Panel,
  ProvenanceBadge,
  Select,
  SyntheticBadge,
  Td,
  TextInput,
  Th,
  Timestamp,
  Tr,
  humanize,
  plural,
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
  const [busy, setBusy] = useState<string | null>(null);
  usePageCrumb(detail.data?.entity.display_name);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  if (!detail.data) return <LoadingState label="Loading entity…" rows={6} />;
  const { entity } = detail.data;
  const analystOwned = entity.origin === "analyst_assertion";

  async function run(key: string, action: () => Promise<unknown>) {
    setError(null);
    setBusy(key);
    try {
      await action();
      await detail.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function addIdentifier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const platform = String(form.get("platform") ?? "").trim();
    await run("identifier", async () => {
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
    await run("link", () =>
      mutate(`${apiBase}/entities/${entityId}/evidence-links`, {
        body: { evidence_id: String(form.get("evidence_id")), note: String(form.get("note") ?? "") || null },
      }),
    );
  }

  async function deleteEntity() {
    setError(null);
    setBusy("delete");
    try {
      await mutate(`${apiBase}/entities/${entityId}`, { method: "DELETE" });
      await refreshCase();
      router.push(`${base}/entities`);
    } catch (caught) {
      setError(describeError(caught));
      setBusy(null);
    }
  }

  const shared = detail.data.shared_identifiers;
  return (
    <div className="space-y-6">
      <PageHeader
        title={entity.display_name}
        meta={
          <>
            <span>{humanize(entity.entity_type)}</span>
            <OriginBadge origin={entity.origin} />
            {entity.attributes.synthetic ? <SyntheticBadge /> : null}
          </>
        }
        actions={
          <>
            <ButtonLink href={`${base}/compare?entity_id=${entity.id}`} icon={GitCompareArrows}>
              Compare
            </ButtonLink>
            {writable && analystOwned ? (
              <ConfirmAction
                label="Delete entity"
                confirmLabel="Delete"
                message="Delete this entity and its identifiers?"
                busy={busy === "delete"}
                onConfirm={() => void deleteEntity()}
              />
            ) : null}
          </>
        }
      />
      <ActionError message={error} />
      {entity.attributes.candidate ? (
        <Notice tone="warn">Candidate account: a connector reported it. That is not evidence that any particular person controls it.</Notice>
      ) : null}

      {shared.length > 0 ? (
        <Panel
          title="Other entities share an identifier"
          description="Shown for review only. Tracehollow never merges entities automatically; a shared username, email or sender label is not proof that they are the same person or account."
          flush
        >
          <ul className="divide-y divide-line text-sm">
            {shared.map((item) => (
              <li key={`${item.entity_id}-${item.identifier_type}-${item.normalized_value}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <TriangleAlert aria-hidden="true" className="size-4 shrink-0 text-warn" />
                  <Link href={`${base}/entities/${item.entity_id}`} className="font-medium break-words text-accent hover:underline">
                    {item.display_name}
                  </Link>
                  <span className="text-muted">
                    {humanize(item.entity_type)} · same {humanize(item.identifier_type).toLowerCase()} <Mono>{item.normalized_value}</Mono>
                  </span>
                </span>
                <Link href={`${base}/compare?entity_id=${entity.id}&entity_id=${item.entity_id}`} className="text-sm text-accent hover:underline">
                  Compare both
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          <Panel title="Identifiers" description="Original values are preserved; normalized values are comparison keys only." flush>
            {entity.identifiers.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No identifiers.</p> : null}
            {entity.identifiers.length > 0 ? (
              <DataTable caption="Identifiers" minWidth="36rem">
                <thead>
                  <tr>
                    <Th>Type</Th>
                    <Th>Original value</Th>
                    <Th>Normalized</Th>
                    {writable && analystOwned ? (
                      <Th>
                        <span className="sr-only">Actions</span>
                      </Th>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {entity.identifiers.map((identifier) => (
                    <Tr key={identifier.id}>
                      <Td className="whitespace-nowrap">
                        {humanize(identifier.identifier_type)}
                        {identifier.platform ? <span className="block text-xs text-muted">{identifier.platform}</span> : null}
                      </Td>
                      <Td>
                        <Mono className="text-ink">{identifier.original_value}</Mono>
                      </Td>
                      <Td>
                        <Mono className="text-muted">{identifier.normalized_value}</Mono>
                      </Td>
                      {writable && analystOwned ? (
                        <Td className="text-right">
                          <ConfirmAction
                            label="Remove"
                            confirmLabel="Remove"
                            message="Remove this identifier?"
                            busy={busy === identifier.id}
                            onConfirm={() => void run(identifier.id, () => mutate(`${apiBase}/entities/${entityId}/identifiers/${identifier.id}`, { method: "DELETE" }))}
                          />
                        </Td>
                      ) : null}
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            ) : null}
            {writable ? (
              <div className="border-t border-line p-4">
                <Disclosure summary="Add an identifier">
                  <form onSubmit={addIdentifier} className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)_10rem_auto] sm:items-end">
                    <Field label="Type" htmlFor="new-identifier-type">
                      <Select id="new-identifier-type" name="identifier_type">
                        {(vocabulary.data?.identifier_types ?? []).map((type) => (
                          <option key={type} value={type}>
                            {humanize(type)}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Value" htmlFor="new-identifier-value">
                      <TextInput id="new-identifier-value" name="value" required />
                    </Field>
                    <Field label="Platform" htmlFor="new-identifier-platform">
                      <TextInput id="new-identifier-platform" name="platform" />
                    </Field>
                    <Button type="submit" icon={Plus} busy={busy === "identifier"} disabled={busy === "identifier"}>
                      Add
                    </Button>
                  </form>
                </Disclosure>
              </div>
            ) : null}
          </Panel>

          <Panel title="Relationships" description="Select a predicate to inspect origin, review history and supporting evidence.">
            <RelationshipTable relationships={detail.data.relationships} onChanged={() => void detail.reload()} />
          </Panel>

          <Panel title="Linked evidence" flush>
            {detail.data.linked_evidence.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No evidence linked yet.</p> : null}
            {detail.data.linked_evidence.length > 0 ? (
              <ul className="divide-y divide-line text-sm">
                {detail.data.linked_evidence.map((link) => (
                  <li key={link.link_id} className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
                    <span className="min-w-0 space-y-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <Link href={`${base}/evidence/${link.evidence_id}`} className="font-medium break-words text-accent hover:underline">
                          {link.title}
                        </Link>
                        <ProvenanceBadge evidence={{ acquisition_method: link.acquisition_method }} />
                      </span>
                      {link.note ? <span className="block text-xs text-muted">{link.note}</span> : null}
                    </span>
                    {writable ? (
                      <ConfirmAction
                        label="Unlink"
                        confirmLabel="Unlink"
                        message="Remove this link?"
                        busy={busy === link.link_id}
                        onConfirm={() => void run(link.link_id, () => mutate(`${apiBase}/entities/${entityId}/evidence-links/${link.link_id}`, { method: "DELETE" }))}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
            {writable ? (
              <form onSubmit={linkEvidence} className="grid gap-3 border-t border-line p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,14rem)_auto] md:items-end">
                <Field label="Evidence in this case" htmlFor="link-evidence">
                  <Select id="link-evidence" name="evidence_id" required>
                    {(evidenceOptions.data?.items ?? []).map((evidence) => (
                      <option key={evidence.id} value={evidence.id}>
                        {evidence.title}
                        {evidence.synthetic ? " (synthetic)" : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Note (optional)" htmlFor="link-note">
                  <TextInput id="link-note" name="note" maxLength={2000} />
                </Field>
                <Button type="submit" icon={Link2} disabled={(evidenceOptions.data?.items.length ?? 0) === 0 || busy === "link"} busy={busy === "link"}>
                  Link evidence
                </Button>
              </form>
            ) : null}
          </Panel>

          <Panel title="Observations" description="Source-specific facts that reference this entity, newest collection first." flush>
            {observations.state === "error" ? (
              <div className="p-4">
                <ErrorNotice error={observations.error} />
              </div>
            ) : null}
            {!observations.data && observations.state !== "error" ? <LoadingState label="Loading observations…" className="p-4" /> : null}
            {observations.data && observations.data.items.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted">No observations reference this entity.</p>
            ) : null}
            <ul className="divide-y divide-line">
              {observations.data?.items.map((observation) => {
                const label = observationLabel(observation);
                return (
                  <li key={observation.id} className="space-y-2 px-4 py-3 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="min-w-0 font-medium break-words text-ink">{label.primary}</span>
                      <span className="text-xs text-muted">
                        {humanize(observation.observation_type)} · collected <Timestamp value={observation.collected_at} />
                      </span>
                    </div>
                    {observation.evidence_id ? (
                      <Link href={`${base}/evidence/${observation.evidence_id}`} className="text-xs text-accent hover:underline">
                        Source evidence
                      </Link>
                    ) : null}
                    <Disclosure summary="Recorded payload">
                      <pre className="max-h-64 overflow-auto font-mono text-code whitespace-pre-wrap break-words text-ink">
                        {JSON.stringify(observation.payload, null, 2)}
                      </pre>
                    </Disclosure>
                  </li>
                );
              })}
            </ul>
          </Panel>

          <NotesPanel subject={{ entity_id: entityId }} title="Notes on this entity" />
        </div>

        <div className="min-w-0 space-y-6 lg:sticky lg:top-20">
          <Panel title="Details">
            <KeyValue
              items={[
                ["Type", humanize(entity.entity_type)],
                ["Origin", <OriginBadge key="origin" origin={entity.origin} />],
                ["Description", entity.description || "None"],
                ["Created", <Timestamp key="created" value={entity.created_at} />],
                [
                  "Created by run",
                  entity.created_by_query_run_id ? (
                    <Link key="run" href={`${base}/runs/${entity.created_by_query_run_id}`} className="text-accent hover:underline">
                      View execution
                    </Link>
                  ) : (
                    "Added by an analyst"
                  ),
                ],
                ["Observations", plural(detail.data.observation_count, "observation")],
              ]}
            />
          </Panel>
          {detail.data.relationships.length === 0 && detail.data.linked_evidence.length === 0 && writable ? (
            <EmptyState title="Not connected yet" action={<ButtonLink href={`${base}/relationships`}>Assert a relationship</ButtonLink>}>
              Link supporting evidence below, or record how this entity relates to others.
            </EmptyState>
          ) : null}
        </div>
      </div>
    </div>
  );
}
