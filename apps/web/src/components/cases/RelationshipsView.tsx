"use client";

import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Entity, Evidence, Page, Relationship, Vocabulary } from "@/lib/workspace-types";

import { Button, ErrorNotice, Field, LoadingState, Pagination, Section, Select, TextInput, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { RelationshipTable } from "./RelationshipTable";

const PAGE_SIZE = 25;

function toIso(value: FormDataEntryValue | null): string | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  // datetime-local has no offset; interpret it in the analyst's browser time zone and send UTC.
  return new Date(text).toISOString();
}

export function RelationshipsView() {
  const { apiBase, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const [offset, setOffset] = useState(0);
  const [origin, setOrigin] = useState("");
  const [review, setReview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (origin) params.set("origin", origin);
  if (review) params.set("review_status", review);
  const relationships = useResource<Page<Relationship>>(`${apiBase}/relationships?${params.toString()}`);
  const entities = useResource<Page<Entity>>(writable ? `${apiBase}/entities?limit=100` : null);
  const evidence = useResource<Page<Evidence>>(writable ? `${apiBase}/evidence?limit=100` : null);
  const vocabulary = useResource<Vocabulary>(`${apiBase}/entity-vocabulary`);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const supporting = form.getAll("supporting_evidence_ids").map(String).filter(Boolean);
    setSaving(true);
    setError(null);
    try {
      await mutate(`${apiBase}/relationships`, {
        body: {
          source_entity_id: String(form.get("source")),
          target_entity_id: String(form.get("target")),
          predicate: String(form.get("predicate") ?? "").trim(),
          description: String(form.get("description") ?? ""),
          valid_from: toIso(form.get("valid_from")),
          valid_to: toIso(form.get("valid_to")),
          supporting_evidence_ids: supporting,
        },
      });
      formElement.reset();
      await Promise.all([relationships.reload(), refreshCase()]);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  const entityOptions = entities.data?.items ?? [];
  return (
    <div className="space-y-6">
      {writable ? (
        <Section
          title="Assert a relationship"
          description="Manual relationships are analyst assertions and start unreviewed. Attach the evidence that supports them."
        >
          {entityOptions.length < 2 && entities.state === "ready" ? (
            <p className="text-sm text-muted">Create at least two entities before adding a relationship.</p>
          ) : (
            <form onSubmit={create} className="space-y-3">
              {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Source entity" htmlFor="rel-source">
                  <Select id="rel-source" name="source" required>
                    {entityOptions.map((entity) => (
                      <option key={entity.id} value={entity.id}>
                        {entity.display_name} ({humanize(entity.entity_type)})
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Predicate" htmlFor="rel-predicate" hint="Lowercase, e.g. owns or links_to">
                  <TextInput id="rel-predicate" name="predicate" list="predicate-suggestions" required pattern="[a-z][a-z0-9_]{1,63}" />
                  <datalist id="predicate-suggestions">
                    {(vocabulary.data?.suggested_predicates ?? []).map((predicate) => (
                      <option key={predicate} value={predicate} />
                    ))}
                  </datalist>
                </Field>
                <Field label="Target entity" htmlFor="rel-target">
                  <Select id="rel-target" name="target" required defaultValue={entityOptions[1]?.id}>
                    {entityOptions.map((entity) => (
                      <option key={entity.id} value={entity.id}>
                        {entity.display_name} ({humanize(entity.entity_type)})
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Valid from (your local time)" htmlFor="rel-from">
                  <TextInput id="rel-from" name="valid_from" type="datetime-local" />
                </Field>
                <Field label="Valid to (your local time)" htmlFor="rel-to">
                  <TextInput id="rel-to" name="valid_to" type="datetime-local" />
                </Field>
                <Field label="Description" htmlFor="rel-description">
                  <TextInput id="rel-description" name="description" maxLength={10000} />
                </Field>
              </div>
              <Field label="Supporting evidence" htmlFor="rel-evidence" hint="Hold Ctrl or Cmd to select several.">
                <Select id="rel-evidence" name="supporting_evidence_ids" multiple size={Math.min(5, Math.max(2, evidence.data?.items.length ?? 2))}>
                  {(evidence.data?.items ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                      {item.synthetic ? " (synthetic)" : ""}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button type="submit" variant="primary" disabled={saving} aria-busy={saving}>
                {saving ? "Saving…" : "Add relationship"}
              </Button>
            </form>
          )}
        </Section>
      ) : null}

      <Section title="Relationships" description="Select a predicate to inspect origin, review history and supporting evidence.">
        <div className="mb-3 flex flex-wrap gap-3">
          <div>
            <label htmlFor="rel-filter-origin" className="block text-xs text-muted">
              Filter by origin
            </label>
            <select
              id="rel-filter-origin"
              value={origin}
              onChange={(event) => {
                setOrigin(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All</option>
              <option value="observed">Observed</option>
              <option value="analyst_assertion">Analyst assertion</option>
            </select>
          </div>
          <div>
            <label htmlFor="rel-filter-review" className="block text-xs text-muted">
              Filter by review status
            </label>
            <select
              id="rel-filter-review"
              value={review}
              onChange={(event) => {
                setReview(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All</option>
              {["unreviewed", "accepted", "rejected", "superseded"].map((state) => (
                <option key={state} value={state}>
                  {humanize(state)}
                </option>
              ))}
            </select>
          </div>
        </div>
        {relationships.state === "error" ? (
          <ErrorNotice error={relationships.error} onRetry={() => void relationships.reload()} />
        ) : null}
        {relationships.state === "loading" && !relationships.data ? <LoadingState /> : null}
        {relationships.data ? (
          <>
            <RelationshipTable relationships={relationships.data.items} onChanged={() => void relationships.reload()} />
            <Pagination total={relationships.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          </>
        ) : null}
      </Section>
    </div>
  );
}
