"use client";

import { Plus, Search, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Entity, Page, Vocabulary } from "@/lib/workspace-types";

import {
  Button,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  FieldGroup,
  FormError,
  IconButton,
  LoadingState,
  Mono,
  OriginBadge,
  PageHeader,
  Pagination,
  Panel,
  Select,
  SyntheticBadge,
  Td,
  TextArea,
  TextInput,
  Th,
  Toolbar,
  Tr,
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

function AddEntityForm({ types, identifierTypes, onCreated, onClose }: { types: string[]; identifierTypes: string[]; onCreated: () => Promise<void>; onClose: () => void }) {
  const { apiBase } = useCase();
  const { mutate } = useSession();
  const [rows, setRows] = useState<IdentifierRow[]>([{ identifier_type: "domain", value: "", platform: "" }]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
      await onCreated();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  function update(index: number, patch: Partial<IdentifierRow>) {
    setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  return (
    <Panel
      title="Add entity"
      description="Manual entities are analyst assertions. Identifiers keep the original value and a normalized comparison value; shared values never merge entities."
      actions={<IconButton icon={X} label="Close form" onClick={onClose} />}
    >
      <form onSubmit={create} className="space-y-5">
        <FormError message={error} />
        <div className="grid gap-4 md:grid-cols-[14rem_minmax(0,1fr)]">
          <Field label="Entity type" htmlFor="entity-type">
            <Select id="entity-type" name="entity_type" required>
              {types.map((type) => (
                <option key={type} value={type}>
                  {humanize(type)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Display name" htmlFor="entity-name">
            <TextInput id="entity-name" name="display_name" required maxLength={300} />
          </Field>
        </div>
        <Field label="Description" htmlFor="entity-description">
          <TextArea id="entity-description" name="description" rows={2} maxLength={10000} />
        </Field>
        <FieldGroup legend="Identifiers" hint="Platform IDs require a platform and are unique within the case; usernames and names are not.">
          <div className="space-y-2">
            {rows.map((row, index) => (
              <div key={index} className="grid gap-2 sm:grid-cols-[10rem_minmax(0,1fr)_minmax(0,12rem)_auto]">
                <label className="sr-only" htmlFor={`identifier-type-${index}`}>
                  Identifier type {index + 1}
                </label>
                <Select id={`identifier-type-${index}`} value={row.identifier_type} onChange={(event) => update(index, { identifier_type: event.target.value })}>
                  {identifierTypes.map((type) => (
                    <option key={type} value={type}>
                      {humanize(type)}
                    </option>
                  ))}
                </Select>
                <label className="sr-only" htmlFor={`identifier-value-${index}`}>
                  Identifier value {index + 1}
                </label>
                <TextInput id={`identifier-value-${index}`} value={row.value} placeholder="Value as observed" onChange={(event) => update(index, { value: event.target.value })} />
                <label className="sr-only" htmlFor={`identifier-platform-${index}`}>
                  Platform {index + 1}
                </label>
                <TextInput id={`identifier-platform-${index}`} value={row.platform} placeholder="Platform (optional)" onChange={(event) => update(index, { platform: event.target.value })} />
                <IconButton
                  icon={Trash2}
                  size="md"
                  label={`Remove identifier ${index + 1}`}
                  onClick={() => setRows(rows.filter((_, i) => i !== index))}
                  disabled={rows.length === 1}
                />
              </div>
            ))}
            <Button size="sm" variant="ghost" icon={Plus} onClick={() => setRows([...rows, { identifier_type: "username", value: "", platform: "" }])}>
              Add identifier
            </Button>
          </div>
        </FieldGroup>
        <Button type="submit" variant="primary" disabled={saving} busy={saving}>
          {saving ? "Saving…" : "Add entity"}
        </Button>
      </form>
    </Panel>
  );
}

export function EntityList() {
  const { apiBase, base, writable, refreshCase } = useCase();
  const [offset, setOffset] = useState(0);
  const [entityType, setEntityType] = useState("");
  const [origin, setOrigin] = useState("");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [adding, setAdding] = useState<boolean | null>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const vocabulary = useResource<Vocabulary>(`${apiBase}/entity-vocabulary`);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (entityType) params.set("entity_type", entityType);
  if (origin) params.set("origin", origin);
  if (applied) params.set("q", applied);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?${params.toString()}`);
  const filtered = Boolean(entityType || origin || applied);
  const types = vocabulary.data?.entity_types ?? [];
  const identifierTypes = vocabulary.data?.identifier_types ?? [];
  // The form starts open while the case has no entities, so the first one is one step away.
  const emptyCase = entities.data?.total === 0 && !filtered;
  const showForm = writable && (adding ?? emptyCase);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Entities"
        description="Organizations, domains, accounts, addresses and other subjects in this case. Observed entities come from sources; analyst entities are assertions. Matching identifiers never merge them."
        actions={
          writable && !showForm ? (
            <Button ref={toggle} variant="primary" icon={Plus} onClick={() => setAdding(true)} aria-controls="add-entity">
              New entity
            </Button>
          ) : undefined
        }
      />

      <div id="add-entity">
        {showForm && vocabulary.data ? (
          <AddEntityForm
            types={types}
            identifierTypes={identifierTypes}
            onCreated={async () => {
              // Keep the form open for the next entity, even when it opened because the case was empty.
              setAdding(true);
              await Promise.all([entities.reload(), refreshCase()]);
            }}
            onClose={() => {
              setAdding(false);
              window.setTimeout(() => toggle.current?.focus(), 0);
            }}
          />
        ) : null}
      </div>

      <Panel title="All entities" flush>
        <form
          role="search"
          className="border-b border-line px-4 py-3"
          onSubmit={(event) => {
            event.preventDefault();
            setOffset(0);
            setApplied(query.trim());
          }}
        >
          <Toolbar>
            <div className="min-w-0 flex-1 basis-64">
              <label htmlFor="entity-search" className="mb-1.5 block text-xs font-medium text-muted">
                Search name or identifier
              </label>
              <div className="flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
                  <TextInput id="entity-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-8" />
                </div>
                <Button type="submit">Search</Button>
              </div>
            </div>
            <div className="w-44">
              <label htmlFor="entity-filter-type" className="mb-1.5 block text-xs font-medium text-muted">
                Type
              </label>
              <Select
                id="entity-filter-type"
                value={entityType}
                onChange={(event) => {
                  setEntityType(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All types</option>
                {types.map((type) => (
                  <option key={type} value={type}>
                    {humanize(type)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-44">
              <label htmlFor="entity-filter-origin" className="mb-1.5 block text-xs font-medium text-muted">
                Origin
              </label>
              <Select
                id="entity-filter-origin"
                value={origin}
                onChange={(event) => {
                  setOrigin(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All origins</option>
                <option value="analyst_assertion">Analyst assertion</option>
                <option value="observed">Observed</option>
              </Select>
            </div>
            {applied ? (
              <Button
                variant="ghost"
                icon={X}
                onClick={() => {
                  setQuery("");
                  setApplied("");
                  setOffset(0);
                }}
              >
                Clear search
              </Button>
            ) : null}
          </Toolbar>
        </form>
        {entities.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={entities.error} onRetry={() => void entities.reload()} />
          </div>
        ) : null}
        {entities.state === "loading" && !entities.data ? <LoadingState label="Loading entities…" rows={5} className="p-4" /> : null}
        {entities.data && entities.data.items.length === 0 ? (
          <div className="p-4">
            {filtered ? (
              <EmptyState compact>No entities match these filters.</EmptyState>
            ) : (
              <EmptyState compact>
                No entities yet. Query runs add observed entities; you can also add the ones you already know.
              </EmptyState>
            )}
          </div>
        ) : null}
        {entities.data && entities.data.items.length > 0 ? (
          <>
            <DataTable caption="Entities" minWidth="44rem">
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Origin</Th>
                  <Th>Identifiers</Th>
                </tr>
              </thead>
              <tbody>
                {entities.data.items.map((entity) => (
                  <Tr key={entity.id}>
                    <Td className="max-w-sm">
                      <Link href={`${base}/entities/${entity.id}`} className="font-medium break-words text-accent hover:underline">
                        {entity.display_name}
                      </Link>
                      {entity.attributes.synthetic ? (
                        <span className="ml-2 inline-block align-middle">
                          <SyntheticBadge />
                        </span>
                      ) : null}
                    </Td>
                    <Td className="whitespace-nowrap text-muted">{humanize(entity.entity_type)}</Td>
                    <Td>
                      <OriginBadge origin={entity.origin} />
                    </Td>
                    <Td className="max-w-md">
                      <ul className="space-y-0.5">
                        {entity.identifiers.slice(0, 3).map((identifier) => (
                          <li key={identifier.id} className="min-w-0">
                            <span className="text-xs text-muted">{humanize(identifier.identifier_type)}</span> <Mono>{identifier.original_value}</Mono>
                          </li>
                        ))}
                      </ul>
                      {entity.identifiers.length > 3 ? <span className="text-xs text-muted">+{entity.identifiers.length - 3} more</span> : null}
                      {entity.identifiers.length === 0 ? <span className="text-xs text-muted">None</span> : null}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
            <Pagination total={entities.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} className="border-t border-line" />
          </>
        ) : null}
      </Panel>
    </div>
  );
}
