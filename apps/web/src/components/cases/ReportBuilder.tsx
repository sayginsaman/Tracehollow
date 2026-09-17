"use client";

import { useState } from "react";

import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ReportPreview } from "@/lib/workspace-types";

import { Button, EmptyState, ErrorNotice, Field, LoadingState, Notice, Section, TextArea, TextInput, formatBytes, humanize } from "../ui";
import { useCase } from "./CaseContext";

interface SelectableItem {
  id: string;
  label: string;
  detail: string;
}

interface Selectable {
  entities: SelectableItem[];
  relationships: SelectableItem[];
  evidence: SelectableItem[];
  ai_answers: SelectableItem[];
  notes: SelectableItem[];
}

export interface ReportForm {
  title: string;
  includePurpose: boolean;
  entityIds: string[];
  relationshipIds: string[];
  evidenceIds: string[];
  aiAnswerIds: string[];
  noteIds: string[];
  compareEntityIds: string[];
  includeTimeline: boolean;
  includeCoverage: boolean;
  redactTerms: string;
  redactIdentifierTypes: string[];
}

export const EMPTY_REPORT: ReportForm = {
  title: "",
  includePurpose: true,
  entityIds: [],
  relationshipIds: [],
  evidenceIds: [],
  aiAnswerIds: [],
  noteIds: [],
  compareEntityIds: [],
  includeTimeline: false,
  includeCoverage: true,
  redactTerms: "",
  redactIdentifierTypes: [],
};

export const REDACTABLE_IDENTIFIER_TYPES = ["phone", "email", "name", "username", "ip", "url", "platform_id"];

/** The API request body for a report form; one redaction term per line. */
export function reportRequest(form: ReportForm): Record<string, unknown> {
  return {
    title: form.title.trim() || null,
    include_case_purpose: form.includePurpose,
    entity_ids: form.entityIds,
    relationship_ids: form.relationshipIds,
    evidence_ids: form.evidenceIds,
    ai_message_ids: form.aiAnswerIds,
    note_ids: form.noteIds,
    comparison_entity_ids: form.compareEntityIds.length >= 2 ? form.compareEntityIds : [],
    include_timeline: form.includeTimeline,
    include_coverage: form.includeCoverage,
    redact_terms: form.redactTerms
      .split("\n")
      .map((term) => term.trim())
      .filter((term) => term.length >= 2),
    redact_identifier_types: form.redactIdentifierTypes,
  };
}

function toggle(values: string[], id: string, max?: number): string[] {
  if (values.includes(id)) return values.filter((value) => value !== id);
  if (max !== undefined && values.length >= max) return values;
  return [...values, id];
}

function Picker({
  legend,
  items,
  selected,
  onChange,
  max,
  empty,
}: {
  legend: string;
  items: SelectableItem[];
  selected: string[];
  onChange: (values: string[]) => void;
  max?: number;
  empty: string;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="text-sm font-medium">
        {legend} {selected.length > 0 ? <span className="text-muted">({selected.length} selected)</span> : null}
      </legend>
      {items.length === 0 ? <EmptyState>{empty}</EmptyState> : null}
      <div className="mt-1 max-h-48 space-y-0.5 overflow-auto rounded-md border border-line p-2">
        {items.map((item) => (
          <label key={item.id} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={selected.includes(item.id)}
              disabled={max !== undefined && !selected.includes(item.id) && selected.length >= max}
              onChange={() => onChange(toggle(selected, item.id, max))}
            />
            <span className="min-w-0 break-words">
              {item.label} <span className="text-xs text-muted">{item.detail}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function ReportBuilder() {
  const { apiBase, caseDetail } = useCase();
  const { session, handleAuthError } = useSession();
  const selectable = useResource<Selectable>(`${apiBase}/reports/selectable`);
  const [form, setForm] = useState<ReportForm>(EMPTY_REPORT);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "download" | null>(null);

  function update(patch: Partial<ReportForm>) {
    setForm((current) => ({ ...current, ...patch }));
    setPreview(null);
  }

  async function makePreview() {
    setBusy("preview");
    setError(null);
    try {
      setPreview(
        await apiRequest<ReportPreview>(`${apiBase}/reports/html/preview`, {
          method: "POST",
          body: reportRequest(form),
          csrfToken: session.csrf_token,
        }),
      );
    } catch (caught) {
      if (!handleAuthError(caught)) setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function download() {
    setBusy("download");
    setError(null);
    try {
      const response = await fetch(`${apiBase}/reports/html`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token, Accept: "text/html" },
        body: JSON.stringify(reportRequest(form)),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") ?? "";
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "tracehollow-report.html";
      const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("The report could not be downloaded. Check the preview for problems and try again.");
    } finally {
      setBusy(null);
    }
  }

  if (selectable.state === "error" && !selectable.data) return <ErrorNotice error={selectable.error} onRetry={() => void selectable.reload()} />;
  if (!selectable.data) return <LoadingState label="Loading case records…" />;
  const data = selectable.data;

  return (
    <div className="space-y-6">
      <Section
        title="Build a report"
        description="Nothing is included unless you select it. The report is a single HTML file with bundled excerpts; it contains no scripts and loads nothing when opened. It is generated on demand and not stored or published."
      >
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Report title" htmlFor="report-title" hint={`Defaults to the case title: ${caseDetail.title}`}>
              <TextInput id="report-title" value={form.title} maxLength={200} onChange={(event) => update({ title: event.target.value })} />
            </Field>
            <div className="flex flex-col justify-end gap-1 text-sm">
              <label className="inline-flex items-center gap-2">
                <input type="checkbox" checked={form.includePurpose} onChange={(event) => update({ includePurpose: event.target.checked })} />
                Case purpose and scope
              </label>
              <label className="inline-flex items-center gap-2">
                <input type="checkbox" checked={form.includeCoverage} onChange={(event) => update({ includeCoverage: event.target.checked })} />
                Collection dates and coverage gaps
              </label>
              <label className="inline-flex items-center gap-2">
                <input type="checkbox" checked={form.includeTimeline} onChange={(event) => update({ includeTimeline: event.target.checked })} />
                Timeline
              </label>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <Picker legend="Entities" items={data.entities} selected={form.entityIds} onChange={(entityIds) => update({ entityIds })} max={50} empty="No entities." />
            <Picker legend="Relationships" items={data.relationships} selected={form.relationshipIds} onChange={(relationshipIds) => update({ relationshipIds })} max={100} empty="No relationships." />
            <Picker legend="Evidence excerpts" items={data.evidence} selected={form.evidenceIds} onChange={(evidenceIds) => update({ evidenceIds })} max={100} empty="No evidence." />
            <Picker legend="Compare entities (2-4)" items={data.entities} selected={form.compareEntityIds} onChange={(compareEntityIds) => update({ compareEntityIds })} max={4} empty="No entities." />
            <Picker legend="AI-generated answers" items={data.ai_answers} selected={form.aiAnswerIds} onChange={(aiAnswerIds) => update({ aiAnswerIds })} max={20} empty="No AI answers." />
            <Picker legend="Analyst notes" items={data.notes} selected={form.noteIds} onChange={(noteIds) => update({ noteIds })} max={100} empty="No notes." />
          </div>
          {form.compareEntityIds.length === 1 ? <Notice tone="warn">Select at least two entities to include a comparison.</Notice> : null}
        </div>
      </Section>

      <Section title="Redaction" description="Applied to every text in the report. Strings shaped like credentials are always removed, and stored credentials and sessions are never read.">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Terms to redact" htmlFor="report-redact-terms" hint="One per line; matched case-insensitively.">
            <TextArea id="report-redact-terms" rows={4} value={form.redactTerms} onChange={(event) => update({ redactTerms: event.target.value })} />
          </Field>
          <fieldset>
            <legend className="text-sm font-medium">Redact every known value of these identifier types</legend>
            <div className="mt-1 grid grid-cols-2 gap-1 text-sm">
              {REDACTABLE_IDENTIFIER_TYPES.map((type) => (
                <label key={type} className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.redactIdentifierTypes.includes(type)}
                    onChange={() => update({ redactIdentifierTypes: toggle(form.redactIdentifierTypes, type) })}
                  />
                  {humanize(type)}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      </Section>

      <Section
        title="Review and download"
        actions={
          <>
            <Button onClick={() => void makePreview()} disabled={busy !== null} aria-busy={busy === "preview"}>
              Preview report
            </Button>
            <Button variant="primary" onClick={() => void download()} disabled={busy !== null || preview === null} aria-busy={busy === "download"}>
              Download HTML
            </Button>
          </>
        }
      >
        {error ? (
          <p role="alert" className="mb-2 text-sm text-bad">
            {error}
          </p>
        ) : null}
        {preview === null ? <EmptyState>Preview the report to review exactly what it contains before downloading.</EmptyState> : null}
        {preview ? (
          <div className="space-y-3">
            <p className="text-sm">
              {Object.entries(preview.counts)
                .map(([key, value]) => `${humanize(key)}: ${value}`)
                .join(" · ")}{" "}
              · {formatBytes(preview.size_bytes)} · redactions {preview.redactions_applied} · credential-like values removed{" "}
              {preview.credential_like_values_removed}
            </p>
            {preview.warnings.map((warning) => (
              <Notice key={warning} tone="warn">
                {warning}
              </Notice>
            ))}
            <iframe
              title="Report preview"
              sandbox=""
              srcDoc={preview.html}
              referrerPolicy="no-referrer"
              className="h-[40rem] w-full rounded-md border border-line bg-white"
            />
          </div>
        ) : null}
      </Section>
    </div>
  );
}
