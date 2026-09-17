"use client";

import { FileJson, FileText, MessageSquareText, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { formatUtcShort } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Evidence, Page } from "@/lib/workspace-types";

import {
  DataTable,
  ErrorNotice,
  KeyValue,
  LoadingState,
  Notice,
  PageHeader,
  Panel,
  ProvenanceBadge,
  Tabs,
  Td,
  Th,
  Tr,
  formatBytes,
  tabPanelProps,
} from "../ui";
import { useCase } from "./CaseContext";
import { EvidenceImportForm } from "./EvidenceImportForm";
import { DocumentImportForm, ProcessingJobsPanel, WhatsAppImportForm } from "./ProcessingImports";

type ImportType = "text" | "whatsapp" | "pdf";

const FORMATS: Record<ImportType, { accepts: string; limit: string; result: string; notes: string[] }> = {
  text: {
    accepts: "UTF-8 text (.txt, .md, .csv, .log) or JSON, as a file or pasted",
    limit: "5 MiB per import",
    result: "Stored immediately as one evidence record with its SHA-256 hash. It is indexed for search and AI questions.",
    notes: ["JSON must parse. Text with NUL bytes or invalid UTF-8 is refused, not guessed."],
  },
  whatsapp: {
    accepts: "WhatsApp chat export: the .txt file, or the .zip exported with media",
    limit: "32 MiB chat text, 128 MiB archive, 2,000 archive members",
    result: "The original is kept unchanged. The worker extracts the chat text; each message becomes an observation that cites its line.",
    notes: [
      "Sender names are labels from the exporting phone, not verified identities. No phone numbers or accounts are inferred from them.",
      "Attachments missing from the export are reported as gaps.",
    ],
  },
  pdf: {
    accepts: "PDF documents",
    limit: "64 MiB and 500 pages per document",
    result: "The PDF is stored unchanged and never rendered here. Embedded text and OCR text become separate records with page references.",
    notes: ["Encrypted or unreadable files are reported, not guessed. OCR runs only in an OCR-enabled worker."],
  },
};

export function ImportsView() {
  const { apiBase, base, writable, refreshCase } = useCase();
  const [type, setType] = useState<ImportType>("text");
  const [jobsKey, setJobsKey] = useState(0);
  const recent = useResource<Page<Evidence>>(`${apiBase}/evidence?acquisition_method=authorized_import&limit=10`);
  const format = FORMATS[type];

  const { reload: reloadRecent } = recent;
  // Derived records (chat text, extracted and OCR text, attachments) appear when processing finishes.
  const settled = useCallback(() => {
    void reloadRecent();
    void refreshCase();
  }, [reloadRecent, refreshCase]);

  function imported(processing: boolean) {
    void recent.reload();
    void refreshCase();
    if (processing) setJobsKey((value) => value + 1);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Imports"
        description="Add material you are authorized to use. Originals are stored byte for byte with a SHA-256 hash and labelled as authorized imports, never as collection."
      />

      {writable ? (
        <Panel title="Import material" flush>
          <Tabs
            label="Import type"
            idPrefix="import-type"
            value={type}
            onChange={setType}
            className="px-2 pt-1"
            items={[
              { value: "text", label: <><FileJson aria-hidden="true" className="size-4" />Text or JSON</> },
              { value: "whatsapp", label: <><MessageSquareText aria-hidden="true" className="size-4" />WhatsApp export</> },
              { value: "pdf", label: <><FileText aria-hidden="true" className="size-4" />PDF document</> },
            ]}
          />
          <div {...tabPanelProps("import-type", type)} className="grid gap-6 p-4 outline-none lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="min-w-0">
              {type === "text" ? <EvidenceImportForm apiBase={apiBase} base={base} onImported={() => imported(false)} /> : null}
              {type === "whatsapp" ? <WhatsAppImportForm apiBase={apiBase} base={base} onImported={() => imported(true)} /> : null}
              {type === "pdf" ? <DocumentImportForm apiBase={apiBase} base={base} onImported={() => imported(true)} /> : null}
            </div>
            <div className="h-fit space-y-3 rounded-md border border-line bg-sunken/60 p-4 text-sm">
              <h3 className="text-sm font-semibold text-ink">Format and limits</h3>
              <KeyValue
                compact
                className="sm:grid-cols-1"
                items={[
                  ["Accepts", format.accepts],
                  ["Default limit", format.limit],
                  ["What happens", format.result],
                ]}
              />
              <ul className="space-y-1.5 border-t border-line pt-3 text-xs text-muted">
                {format.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
              <p className="flex items-start gap-2 border-t border-line pt-3 text-xs text-muted">
                <ShieldCheck aria-hidden="true" className="mt-px size-4 shrink-0" />
                Processing runs in a worker without internet access. Imported content is treated as untrusted and shown only as text.
              </p>
            </div>
          </div>
        </Panel>
      ) : (
        <Notice>This case is read-only, so nothing can be imported. Existing imports and processing results remain available.</Notice>
      )}

      <Panel
        title="Processing jobs"
        description="WhatsApp exports and PDFs are processed in the background. Jobs that need a decision wait here; failed or partial jobs say why."
      >
        <ProcessingJobsPanel key={jobsKey} apiBase={apiBase} base={base} writable={writable} onSettled={settled} />
      </Panel>

      <Panel
        title="Recent imports"
        flush
        actions={
          <Link href={`${base}/evidence`} className="text-sm text-accent hover:underline">
            All evidence
          </Link>
        }
      >
        {recent.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={recent.error} onRetry={() => void recent.reload()} />
          </div>
        ) : null}
        {recent.state === "loading" && !recent.data ? <LoadingState label="Loading recent imports…" className="p-4" /> : null}
        {recent.data && recent.data.items.length === 0 ? <p className="px-4 py-4 text-sm text-muted">Nothing has been imported into this case yet.</p> : null}
        {recent.data && recent.data.items.length > 0 ? (
          <DataTable caption="Recent imports" minWidth="40rem">
            <thead>
              <tr>
                <Th>Title</Th>
                <Th>Provenance</Th>
                <Th>Kind</Th>
                <Th>Imported (UTC)</Th>
                <Th className="text-right">Size</Th>
              </tr>
            </thead>
            <tbody>
              {recent.data.items.map((item) => (
                <Tr key={item.id}>
                  <Td className="max-w-md">
                    <Link href={`${base}/evidence/${item.id}`} className="font-medium break-words text-accent hover:underline">
                      {item.title}
                    </Link>
                  </Td>
                  <Td>
                    <ProvenanceBadge evidence={item} />
                  </Td>
                  <Td className="text-muted uppercase">{item.kind}</Td>
                  <Td className="whitespace-nowrap text-muted">{formatUtcShort(item.collected_at)}</Td>
                  <Td className="text-right whitespace-nowrap text-muted">{formatBytes(item.size_bytes)}</Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        ) : null}
      </Panel>
    </div>
  );
}
