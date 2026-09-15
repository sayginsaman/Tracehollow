"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Entity, Page, Vocabulary } from "@/lib/workspace-types";

import {
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  LoadingState,
  Mono,
  OriginBadge,
  Pagination,
  Section,
  Select,
  SyntheticBadge,
  TextArea,
  TextInput,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";

const PAGE_SIZE = 25;

export interface IdentifierRow {
  identifier_type: string;
  value: string;
  platform: string;
}

export function identifierPayload(rows: IdentifierRow[]) {
  return rows
    .filter((row) => row.value.trim())
    .map((row) => ({
      identifier_type: row.identifier_type,
      value: row.value.trim(),
      ...(row.platform.trim() ? { platform: row.platform.trim() } : {}),
    }));
}

export function EntityList() {
  const { apiBase, base, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const [offset, setOffset] = useState(0);
  const [entityType, setEntityType] = useState("");
  const [origin, setOrigin] = useState("");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [rows, setRows] = useState<IdentifierRow[]>([{ identifier_type: "domain", value: "", platform: "" }]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const vocabulary = useResource<Vocabulary>(`${apiBase}/entity-vocabulary`);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (entityType) params.set("entity_type", entityType);
  if (origin) params.set("origin", origin);
  if (applied) params.set("q", applied);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?${params.toString()}`);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setSaving(true);
    setError(null);
    try {
      await mutate(`${apiBase}/entities`, {
        body: {
          entity_type: String(form.get("entity_type")),
          display_name: String(form.get("display_name") ?? ""),
          description: String(form.get("description") ?? ""),
          identifiers: identifierPayload(rows),
        },
      });
      formElement.reset();
      setRows([{ identifier_type: "domain", value: "", platform: "" }]);
      await Promise.all([entities.reload(), refreshCase()]);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  const types = vocabulary.data?.entity_types ?? [];
  const identifierTypes = vocabulary.data?.identifier_types ?? [];

  return (
    <div className="space-y-6">
      {writable ? (
        <Section
          title="Add entity"
          description="Manual entities are analyst assertions. Identifiers keep the original value and a normalized comparison value; shared values never merge entities."
        >
          <form onSubmit={create} className="space-y-3">
            {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="Entity type" htmlFor="entity-type">
                <Select id="entity-type" name="entity_type" required>
                  {types.map((type) => (
                    <option key={type} value={type}>
                      {humanize(type)}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="md:col-span-2">
                <Field label="Display name" htmlFor="entity-name">
                  <TextInput id="entity-name" name="display_name" required maxLength={300} />
                </Field>
              </div>
            </div>
            <Field label="Description" htmlFor="entity-description">
              <TextArea id="entity-description" name="description" rows={2} maxLength={10000} />
            </Field>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Identifiers</legend>
              {rows.map((row, index) => (
                <div key={index} className="grid gap-2 md:grid-cols-[10rem_1fr_12rem_auto]">
                  <label className="sr-only" htmlFor={`identifier-type-${index}`}>
                    Identifier type {index + 1}
                  </label>
                  <select
                    id={`identifier-type-${index}`}
                    value={row.identifier_type}
                    onChange={(event) =>
                      setRows(rows.map((r, i) => (i === index ? { ...r, identifier_type: event.target.value } : r)))
                    }
                    className="rounded-md border border-line bg-surface px-2 py-2 text-sm"
                  >
                    {identifierTypes.map((type) => (
                      <option key={type} value={type}>
                        {humanize(type)}
                      </option>
                    ))}
                  </select>
                  <label className="sr-only" htmlFor={`identifier-value-${index}`}>
                    Identifier value {index + 1}
                  </label>
                  <input
                    id={`identifier-value-${index}`}
                    value={row.value}
                    placeholder="Value as observed"
                    onChange={(event) => setRows(rows.map((r, i) => (i === index ? { ...r, value: event.target.value } : r)))}
                    className="rounded-md border border-line bg-surface px-2 py-2 text-sm"
                  />
                  <label className="sr-only" htmlFor={`identifier-platform-${index}`}>
                    Platform {index + 1}
                  </label>
                  <input
                    id={`identifier-platform-${index}`}
                    value={row.platform}
                    placeholder="Platform (optional)"
                    onChange={(event) => setRows(rows.map((r, i) => (i === index ? { ...r, platform: event.target.value } : r)))}
                    className="rounded-md border border-line bg-surface px-2 py-2 text-sm"
                  />
                  <Button onClick={() => setRows(rows.filter((_, i) => i !== index))} disabled={rows.length === 1}>
                    Remove
                  </Button>
                </div>
              ))}
              <Button onClick={() => setRows([...rows, { identifier_type: "username", value: "", platform: "" }])}>
                Add identifier
              </Button>
              <p className="text-xs text-muted">
                Platform IDs require a platform and are unique within the case; usernames and names are not.
              </p>
            </fieldset>
            <Button type="submit" variant="primary" disabled={saving} aria-busy={saving}>
              {saving ? "Saving…" : "Add entity"}
            </Button>
          </form>
        </Section>
      ) : null}

      <Section title="Entities">
        <form
          role="search"
          className="mb-3 flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setOffset(0);
            setApplied(query.trim());
          }}
        >
          <div>
            <label htmlFor="entity-filter-type" className="block text-xs text-muted">
              Filter by type
            </label>
            <select
              id="entity-filter-type"
              value={entityType}
              onChange={(event) => {
                setEntityType(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All types</option>
              {types.map((type) => (
                <option key={type} value={type}>
                  {humanize(type)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="entity-filter-origin" className="block text-xs text-muted">
              Filter by origin
            </label>
            <select
              id="entity-filter-origin"
              value={origin}
              onChange={(event) => {
                setOrigin(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All origins</option>
              <option value="analyst_assertion">Analyst assertion</option>
              <option value="observed">Observed</option>
            </select>
          </div>
          <div>
            <label htmlFor="entity-search" className="block text-xs text-muted">
              Search name or identifier
            </label>
            <input
              id="entity-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </div>
          <Button type="submit">Filter</Button>
        </form>
        {entities.state === "error" ? <ErrorNotice error={entities.error} onRetry={() => void entities.reload()} /> : null}
        {entities.state === "loading" && !entities.data ? <LoadingState label="Loading entities…" /> : null}
        {entities.data && entities.data.items.length === 0 ? <EmptyState>No entities match.</EmptyState> : null}
        {entities.data && entities.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Entities</caption>
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Name</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Type</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Origin</th>
                  <th scope="col" className="py-2 font-medium">Identifiers</th>
                </tr>
              </thead>
              <tbody>
                {entities.data.items.map((entity) => (
                  <tr key={entity.id} className="border-t border-line align-top">
                    <td className="py-2 pr-4">
                      <Link href={`${base}/entities/${entity.id}`} className="font-medium text-accent hover:underline">
                        {entity.display_name}
                      </Link>
                      {entity.attributes.synthetic ? <span className="ml-2"><SyntheticBadge /></span> : null}
                    </td>
                    <td className="py-2 pr-4">{humanize(entity.entity_type)}</td>
                    <td className="py-2 pr-4">
                      <OriginBadge origin={entity.origin} />
                    </td>
                    <td className="py-2">
                      {entity.identifiers.slice(0, 3).map((identifier) => (
                        <div key={identifier.id}>
                          <span className="text-xs text-muted">{humanize(identifier.identifier_type)}:</span>{" "}
                          <Mono>{identifier.original_value}</Mono>
                        </div>
                      ))}
                      {entity.identifiers.length > 3 ? (
                        <span className="text-xs text-muted">+{entity.identifiers.length - 3} more</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={entities.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Section>
    </div>
  );
}
