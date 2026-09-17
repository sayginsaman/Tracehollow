"use client";

import { Plus, X } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Entity, Evidence, Page, Relationship, Vocabulary } from "@/lib/workspace-types";

import {
  Button,
  ChoiceField,
  ErrorNotice,
  Field,
  FieldGroup,
  FormError,
  IconButton,
  LoadingState,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  Select,
  TextInput,
  Toolbar,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";
import { RelationshipTable } from "./RelationshipTable";

const PAGE_SIZE = 25;

function toIso(value: FormDataEntryValue | null): string | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  // datetime-local has no offset; interpret it in the analyst's browser time zone and send UTC.
  return new Date(text).toISOString();
}

function NewRelationshipForm({
  entities,
  evidence,
  predicates,
  onCreated,
  onClose,
}: {
  entities: Entity[];
  evidence: Evidence[];
  predicates: string[];
  onCreated: () => Promise<void>;
  onClose: () => void;
}) {
  const { apiBase } = useCase();
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [supporting, setSupporting] = useState<string[]>([]);
  const [evidenceFilter, setEvidenceFilter] = useState("");
  const shownEvidence = useMemo(() => {
    const needle = evidenceFilter.trim().toLocaleLowerCase("tr");
    return needle ? evidence.filter((item) => item.title.toLocaleLowerCase("tr").includes(needle)) : evidence;
  }, [evidence, evidenceFilter]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
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
      setSupporting([]);
      await onCreated();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="Assert a relationship"
      description="Manual relationships are analyst assertions and start unreviewed. Attach the evidence that supports them."
      actions={<IconButton icon={X} label="Close form" onClick={onClose} />}
    >
      {entities.length < 2 ? (
        <p className="text-sm text-muted">Create at least two entities before adding a relationship.</p>
      ) : (
        <form onSubmit={create} className="space-y-5">
          <FormError message={error} />
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_14rem_minmax(0,1fr)]">
            <Field label="Source entity" htmlFor="rel-source">
              <Select id="rel-source" name="source" required>
                {entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.display_name} ({humanize(entity.entity_type)})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Predicate" htmlFor="rel-predicate" hint="Lowercase, for example owns or links_to">
              <TextInput id="rel-predicate" name="predicate" list="predicate-suggestions" required pattern="[a-z][a-z0-9_]{1,63}" className="font-mono" />
              <datalist id="predicate-suggestions">
                {predicates.map((predicate) => (
                  <option key={predicate} value={predicate} />
                ))}
              </datalist>
            </Field>
            <Field label="Target entity" htmlFor="rel-target">
              <Select id="rel-target" name="target" required defaultValue={entities[1]?.id}>
                {entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.display_name} ({humanize(entity.entity_type)})
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
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
          <FieldGroup legend="Supporting evidence" hint={`${supporting.length} selected. The relationship cites these records.`}>
            {evidence.length === 0 ? (
              <p className="text-sm text-muted">This case has no evidence yet; the relationship will start without support.</p>
            ) : (
              <div className="rounded-md border border-line">
                {evidence.length > 8 ? (
                  <div className="border-b border-line p-2">
                    <label htmlFor="rel-evidence-filter" className="sr-only">
                      Filter evidence by title
                    </label>
                    <TextInput
                      id="rel-evidence-filter"
                      type="search"
                      placeholder="Filter by title"
                      value={evidenceFilter}
                      onChange={(event) => setEvidenceFilter(event.target.value)}
                    />
                  </div>
                ) : null}
                <div className="max-h-56 space-y-2 overflow-y-auto p-3">
                  {shownEvidence.map((item) => (
                    <ChoiceField
                      key={item.id}
                      checked={supporting.includes(item.id)}
                      onChange={() =>
                        setSupporting(supporting.includes(item.id) ? supporting.filter((id) => id !== item.id) : [...supporting, item.id])
                      }
                      label={`${item.title}${item.synthetic ? " (synthetic)" : ""}`}
                    />
                  ))}
                  {shownEvidence.length === 0 ? <p className="text-sm text-muted">No evidence title matches the filter.</p> : null}
                </div>
              </div>
            )}
          </FieldGroup>
          <Button type="submit" variant="primary" disabled={saving} busy={saving}>
            {saving ? "Saving…" : "Add relationship"}
          </Button>
        </form>
      )}
    </Panel>
  );
}

export function RelationshipsView() {
  const { apiBase, writable, refreshCase } = useCase();
  const [offset, setOffset] = useState(0);
  const [origin, setOrigin] = useState("");
  const [review, setReview] = useState("");
  const [adding, setAdding] = useState<boolean | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (origin) params.set("origin", origin);
  if (review) params.set("review_status", review);
  const relationships = useResource<Page<Relationship>>(`${apiBase}/relationships?${params.toString()}`);
  const entities = useResource<Page<Entity>>(writable ? `${apiBase}/entities?limit=100` : null);
  const evidence = useResource<Page<Evidence>>(writable ? `${apiBase}/evidence?limit=100` : null);
  const vocabulary = useResource<Vocabulary>(`${apiBase}/entity-vocabulary`);
  const filtered = Boolean(origin || review);
  const emptyCase = relationships.data?.total === 0 && !filtered;
  const showForm = writable && (adding ?? emptyCase);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Relationships"
        description="Typed links between entities. Observed links come from sources; analyst assertions and AI suggestions stay unreviewed until someone decides."
        actions={
          writable && !showForm ? (
            <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>
              New relationship
            </Button>
          ) : undefined
        }
      />

      {showForm ? (
        entities.data && evidence.data ? (
          <NewRelationshipForm
            entities={entities.data.items}
            evidence={evidence.data.items}
            predicates={vocabulary.data?.suggested_predicates ?? []}
            onCreated={async () => {
              setAdding(true);
              await Promise.all([relationships.reload(), refreshCase()]);
            }}
            onClose={() => setAdding(false)}
          />
        ) : (
          <LoadingState label="Loading entities and evidence…" />
        )
      ) : null}

      <Panel title="All relationships" flush>
        <div className="border-b border-line px-4 py-3">
          <Toolbar>
            <div className="w-48">
              <label htmlFor="rel-filter-origin" className="mb-1.5 block text-xs font-medium text-muted">
                Origin
              </label>
              <Select
                id="rel-filter-origin"
                value={origin}
                onChange={(event) => {
                  setOrigin(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All origins</option>
                <option value="observed">Observed</option>
                <option value="analyst_assertion">Analyst assertion</option>
                <option value="ai_suggestion">AI suggestion</option>
              </Select>
            </div>
            <div className="w-48">
              <label htmlFor="rel-filter-review" className="mb-1.5 block text-xs font-medium text-muted">
                Review status
              </label>
              <Select
                id="rel-filter-review"
                value={review}
                onChange={(event) => {
                  setReview(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All review states</option>
                {["unreviewed", "accepted", "rejected", "superseded"].map((state) => (
                  <option key={state} value={state}>
                    {humanize(state)}
                  </option>
                ))}
              </Select>
            </div>
          </Toolbar>
        </div>
        <div className="p-4">
          {relationships.state === "error" ? <ErrorNotice error={relationships.error} onRetry={() => void relationships.reload()} /> : null}
          {relationships.state === "loading" && !relationships.data ? <LoadingState label="Loading relationships…" rows={4} /> : null}
          {relationships.data && relationships.data.items.length === 0 ? (
            <Notice>{filtered ? "No relationships match these filters." : "No relationships yet. Query runs add observed ones; you can assert others above."}</Notice>
          ) : null}
          {relationships.data && relationships.data.items.length > 0 ? (
            <RelationshipTable relationships={relationships.data.items} onChanged={() => void relationships.reload()} split />
          ) : null}
        </div>
        {relationships.data ? (
          <Pagination total={relationships.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} className="border-t border-line" />
        ) : null}
      </Panel>
    </div>
  );
}
