"use client";

import Link from "next/link";
import { useState } from "react";

import { formatUtc } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Evidence, Page } from "@/lib/workspace-types";

import { EmptyState, ErrorNotice, LoadingState, Mono, Pagination, Section, SyntheticBadge, formatBytes, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { EvidenceImportForm } from "./EvidenceImportForm";

const PAGE_SIZE = 25;

export function EvidenceList() {
  const { apiBase, base, writable, refreshCase } = useCase();
  const [offset, setOffset] = useState(0);
  const [method, setMethod] = useState("");
  const [kind, setKind] = useState("");

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (method) params.set("acquisition_method", method);
  if (kind) params.set("kind", kind);
  const evidence = useResource<Page<Evidence>>(`${apiBase}/evidence?${params.toString()}`);

  return (
    <div className="space-y-6">
      {writable ? (
        <Section
          title="Import evidence"
          description="Authorized imports are stored byte-for-byte on the evidence volume with a SHA-256 hash and are labelled as imports, not collection."
        >
          <EvidenceImportForm
            apiBase={apiBase}
            base={base}
            onImported={() => {
              void evidence.reload();
              void refreshCase();
            }}
          />
        </Section>
      ) : null}

      <Section title="Evidence">
        <div className="mb-3 flex flex-wrap gap-3">
          <div>
            <label htmlFor="evidence-filter-method" className="block text-xs text-muted">
              Filter by acquisition
            </label>
            <select
              id="evidence-filter-method"
              value={method}
              onChange={(event) => {
                setMethod(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All</option>
              <option value="authorized_import">Authorized import</option>
              <option value="synthetic_fixture">Synthetic fixture</option>
            </select>
          </div>
          <div>
            <label htmlFor="evidence-filter-kind" className="block text-xs text-muted">
              Filter by kind
            </label>
            <select
              id="evidence-filter-kind"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All</option>
              <option value="text">Text</option>
              <option value="json">JSON</option>
            </select>
          </div>
        </div>
        {evidence.state === "error" ? <ErrorNotice error={evidence.error} onRetry={() => void evidence.reload()} /> : null}
        {evidence.state === "loading" && !evidence.data ? <LoadingState label="Loading evidence…" /> : null}
        {evidence.data && evidence.data.items.length === 0 ? <EmptyState>No evidence in this view.</EmptyState> : null}
        {evidence.data && evidence.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Evidence</caption>
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Title</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Acquisition</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Collected / imported</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Size</th>
                  <th scope="col" className="py-2 font-medium">SHA-256</th>
                </tr>
              </thead>
              <tbody>
                {evidence.data.items.map((item) => (
                  <tr key={item.id} className="border-t border-line align-top">
                    <td className="py-2 pr-4">
                      <Link href={`${base}/evidence/${item.id}`} className="font-medium text-accent hover:underline">
                        {item.title}
                      </Link>
                      <div className="text-xs text-muted">{item.kind.toUpperCase()}</div>
                    </td>
                    <td className="py-2 pr-4">
                      {item.synthetic ? <SyntheticBadge /> : humanize(item.acquisition_method)}
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs">{formatUtc(item.collected_at)}</td>
                    <td className="py-2 pr-4">{formatBytes(item.size_bytes)}</td>
                    <td className="py-2">
                      <Mono>{item.sha256.slice(0, 16)}…</Mono>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={evidence.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Section>
    </div>
  );
}
