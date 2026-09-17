"use client";

import { ArrowRight, Eye, FileUp, Search, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { formatUtcShort } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Evidence, EvidencePreview, Page } from "@/lib/workspace-types";

import {
  Button,
  ButtonLink,
  CopyButton,
  DataTable,
  EmptyState,
  ErrorNotice,
  IconButton,
  KeyValue,
  LoadingState,
  PageHeader,
  Pagination,
  Panel,
  ProvenanceBadge,
  SegmentedFilter,
  Select,
  Td,
  TextInput,
  Th,
  Timestamp,
  Toolbar,
  Tr,
  evidenceProvenance,
  formatBytes,
} from "../ui";
import { useCase } from "./CaseContext";

const PAGE_SIZE = 25;

const ACQUISITION_FILTERS = [
  { value: "", label: "All" },
  { value: "connector_collection", label: "Collected" },
  { value: "authorized_import", label: "Imported" },
  { value: "synthetic_fixture", label: "Synthetic" },
];

function Inspector({ evidence, onClose }: { evidence: Evidence; onClose: () => void }) {
  const { apiBase, base } = useCase();
  const preview = useResource<EvidencePreview>(`${apiBase}/evidence/${evidence.id}/preview`);
  const provenance = evidenceProvenance(evidence);
  const text = preview.data ? (preview.data.pretty_json ?? preview.data.text) : "";
  return (
    <aside aria-label="Evidence quick look" className="min-w-0 rounded-lg border border-line bg-surface xl:sticky xl:top-20">
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">Quick look</p>
          <h2 className="text-heading font-semibold break-words text-ink">{evidence.title}</h2>
        </div>
        <IconButton icon={X} label="Close quick look" onClick={onClose} />
      </div>
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap gap-1.5">
          <ProvenanceBadge evidence={evidence} />
        </div>
        <p className="text-sm text-muted">{provenance.description}.</p>
        <KeyValue
          compact
          items={[
            [evidence.acquisition_method === "authorized_import" ? "Imported" : "Collected", <Timestamp key="at" value={evidence.collected_at} />],
            ["Source published", evidence.source_published_at ? <Timestamp key="published" value={evidence.source_published_at} /> : "Unknown"],
            ["Kind", `${evidence.kind.toUpperCase()} · ${formatBytes(evidence.size_bytes)}`],
            [
              "SHA-256",
              <span key="sha" className="inline-flex items-center gap-1">
                <span className="font-mono text-code">{evidence.sha256.slice(0, 16)}…</span>
                <CopyButton value={evidence.sha256} label="Copy SHA-256" />
              </span>,
            ],
          ]}
        />
        {preview.state === "error" ? <ErrorNotice error={preview.error} onRetry={() => void preview.reload()} /> : null}
        {!preview.data && preview.state !== "error" ? <LoadingState label="Loading preview…" rows={4} /> : null}
        {preview.data && preview.data.previewable === false ? (
          <p className="rounded-md bg-sunken px-3 py-2 text-sm text-muted">{preview.data.note ?? "Binary original: it is not decoded or rendered here."}</p>
        ) : null}
        {preview.data && preview.data.previewable !== false ? (
          <pre
            tabIndex={0}
            aria-label="Quick look preview, shown as plain text"
            className="max-h-80 overflow-auto rounded-md border border-line bg-sunken p-3 font-mono text-code whitespace-pre-wrap break-words text-ink"
          >
            {text.length > 4000 ? `${text.slice(0, 4000)}…` : text}
          </pre>
        ) : null}
        <ButtonLink href={`${base}/evidence/${evidence.id}`} variant="primary" icon={ArrowRight} className="w-full">
          Open full record
        </ButtonLink>
      </div>
    </aside>
  );
}

export function EvidenceList() {
  const { apiBase, base, writable } = useCase();
  const [offset, setOffset] = useState(0);
  const [method, setMethod] = useState("");
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [selected, setSelected] = useState<Evidence | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (method) params.set("acquisition_method", method);
  if (kind) params.set("kind", kind);
  if (applied) params.set("q", applied);
  const evidence = useResource<Page<Evidence>>(`${apiBase}/evidence?${params.toString()}`);
  const filtered = Boolean(method || kind || applied);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evidence"
        description="Every stored original and derived record with where it came from. Collected material, authorized imports, extracted text and OCR text are labelled separately."
        actions={
          writable ? (
            <ButtonLink href={`${base}/imports`} icon={FileUp}>
              Import material
            </ButtonLink>
          ) : undefined
        }
      />

      <div className={selected ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]" : ""}>
        <Panel title="Evidence records" flush>
          <div className="space-y-3 border-b border-line px-4 py-3">
            <Toolbar>
              <form
                role="search"
                className="flex min-w-0 flex-1 basis-64 gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  setOffset(0);
                  setApplied(query.trim());
                }}
              >
                <label htmlFor="evidence-search" className="sr-only">
                  Search evidence titles
                </label>
                <div className="relative min-w-0 flex-1">
                  <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
                  <TextInput id="evidence-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search titles" className="pl-8" />
                </div>
                <Button type="submit">Search</Button>
              </form>
              <div className="flex items-center gap-2">
                <label htmlFor="evidence-filter-kind" className="text-sm whitespace-nowrap text-muted">
                  Kind
                </label>
                <Select
                  id="evidence-filter-kind"
                  value={kind}
                  onChange={(event) => {
                    setKind(event.target.value);
                    setOffset(0);
                  }}
                  className="w-40"
                >
                  <option value="">All kinds</option>
                  <option value="text">Text</option>
                  <option value="json">JSON</option>
                  <option value="html">HTML snapshot</option>
                  <option value="pdf">PDF</option>
                  <option value="archive">Archive</option>
                  <option value="binary">Other file</option>
                </Select>
              </div>
            </Toolbar>
            <div className="flex flex-wrap items-center gap-2">
              <SegmentedFilter
                label="Filter by acquisition"
                options={ACQUISITION_FILTERS}
                value={method}
                onChange={(value) => {
                  setMethod(value);
                  setOffset(0);
                }}
              />
              {applied ? (
                <Button
                  size="sm"
                  variant="ghost"
                  icon={X}
                  onClick={() => {
                    setQuery("");
                    setApplied("");
                    setOffset(0);
                  }}
                >
                  Clear search “{applied}”
                </Button>
              ) : null}
            </div>
          </div>

          {evidence.state === "error" ? (
            <div className="p-4">
              <ErrorNotice error={evidence.error} onRetry={() => void evidence.reload()} />
            </div>
          ) : null}
          {evidence.state === "loading" && !evidence.data ? <LoadingState label="Loading evidence…" rows={5} className="p-4" /> : null}
          {evidence.data && evidence.data.items.length === 0 ? (
            <div className="p-4">
              {filtered ? (
                <EmptyState compact>No evidence matches these filters.</EmptyState>
              ) : (
                <EmptyState
                  title="No evidence yet"
                  action={
                    writable ? (
                      <ButtonLink href={`${base}/imports`} variant="primary" icon={FileUp}>
                        Import material
                      </ButtonLink>
                    ) : undefined
                  }
                >
                  Evidence arrives from query runs and from imports. Each record keeps its original bytes, SHA-256 hash and provenance.
                </EmptyState>
              )}
            </div>
          ) : null}
          {evidence.data && evidence.data.items.length > 0 ? (
            <>
              <DataTable caption="Evidence" minWidth={selected ? "40rem" : "52rem"}>
                <thead>
                  <tr>
                    <Th>Title</Th>
                    <Th>Provenance</Th>
                    <Th>Collected or imported (UTC)</Th>
                    {selected ? null : <Th className="text-right">Size</Th>}
                    {selected ? null : <Th>SHA-256</Th>}
                    <Th>
                      <span className="sr-only">Quick look</span>
                    </Th>
                  </tr>
                </thead>
                <tbody>
                  {evidence.data.items.map((item) => (
                    <Tr key={item.id} selected={selected?.id === item.id}>
                      <Td className="max-w-md">
                        <Link href={`${base}/evidence/${item.id}`} className="font-medium break-words text-accent hover:underline">
                          {item.title}
                        </Link>
                        <div className="mt-0.5 text-xs text-muted uppercase">{item.kind}</div>
                      </Td>
                      <Td>
                        <ProvenanceBadge evidence={item} />
                      </Td>
                      <Td className="whitespace-nowrap text-muted">{formatUtcShort(item.collected_at)}</Td>
                      {selected ? null : <Td className="text-right whitespace-nowrap text-muted">{formatBytes(item.size_bytes)}</Td>}
                      {selected ? null : (
                        <Td className="whitespace-nowrap">
                          <span className="inline-flex items-center gap-1">
                            <span className="font-mono text-code text-muted" title={item.sha256}>
                              {item.sha256.slice(0, 12)}…
                            </span>
                            <CopyButton value={item.sha256} label={`Copy SHA-256 of ${item.title}`} />
                          </span>
                        </Td>
                      )}
                      <Td className="text-right">
                        <IconButton
                          icon={Eye}
                          label={`Quick look: ${item.title}`}
                          aria-pressed={selected?.id === item.id}
                          onClick={() => setSelected(selected?.id === item.id ? null : item)}
                        />
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
              <Pagination total={evidence.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} className="border-t border-line" />
            </>
          ) : null}
        </Panel>
        {selected ? <Inspector key={selected.id} evidence={selected} onClose={() => setSelected(null)} /> : null}
      </div>
    </div>
  );
}
