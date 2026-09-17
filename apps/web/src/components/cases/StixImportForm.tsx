"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/client-api";
import { describeError } from "@/lib/messages";
import { useSession } from "@/lib/session-context";
import type { StixImportResult } from "@/lib/workspace-types";

import { ActionError, Button, ChoiceField, Field, FieldGroup, Mono, Notice, TextArea, TextInput, humanize, plural } from "../ui";

export const STIX_MAX_BYTES = 5 * 1024 * 1024;

function countsText(counts: Record<string, number>): string {
  return (
    Object.entries(counts)
      .filter(([, value]) => value > 0)
      .map(([key, value]) => `${value} ${humanize(key).toLowerCase()}`)
      .join(", ") || "none"
  );
}

interface RejectedObject {
  id?: string;
  type?: string;
  reason?: string;
}

function rejectedObjects(error: unknown): RejectedObject[] {
  if (!(error instanceof ApiError) || !error.payload || typeof error.payload !== "object") return [];
  const detail = (error.payload as { detail?: { objects?: unknown } }).detail;
  return Array.isArray(detail?.objects) ? (detail.objects as RejectedObject[]) : [];
}

export function StixImportForm({ apiBase, base, onImported }: { apiBase: string; base: string; onImported: () => void }) {
  const { mutate } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [origin, setOrigin] = useState("");
  const [strict, setStrict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejected, setRejected] = useState<RejectedObject[]>([]);
  const [result, setResult] = useState<StixImportResult | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setResult(null);
    setRejected([]);
    if (!file) return setError("Choose a STIX 2.1 bundle file.");
    if (file.size > STIX_MAX_BYTES) return setError("The bundle is larger than 5 MiB.");
    if (origin.trim().length < 3) return setError("Describe where the bundle came from and why you may use it.");
    const form = new FormData();
    form.set("file", file);
    form.set("import_origin", origin.trim());
    form.set("on_unsupported", strict ? "reject" : "skip");
    const element = event.currentTarget;
    setSaving(true);
    setError(null);
    try {
      setResult(await mutate<StixImportResult>(`${apiBase}/imports/stix`, { form }));
      element.reset();
      setFile(null);
      setOrigin("");
      onImported();
    } catch (caught) {
      setError(describeError(caught));
      setRejected(rejectedObjects(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-5" aria-label="Import a STIX 2.1 bundle">
      <Field label="Bundle file" htmlFor="stix-file" hint="A JSON STIX 2.1 bundle. URLs inside it are recorded, never opened.">
        <TextInput id="stix-file" type="file" accept=".json,application/json,application/stix+json" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </Field>
      <Field label="Origin and authorization" htmlFor="stix-origin" hint="Who produced the bundle and why this case may use it. Stored with the import.">
        <TextArea id="stix-origin" value={origin} onChange={(event) => setOrigin(event.target.value)} maxLength={2000} rows={3} />
      </Field>
      <FieldGroup legend="Unsupported objects">
        <ChoiceField
          label="Reject the whole bundle if it contains unsupported objects"
          description="Otherwise unsupported objects (malware, indicators, reports and others) are skipped and listed."
          checked={strict}
          onChange={(event) => setStrict(event.target.checked)}
        />
      </FieldGroup>
      <ActionError message={error} />
      {rejected.length ? (
        <ul className="max-h-48 space-y-1 overflow-auto rounded-md bg-sunken p-3 text-xs text-ink">
          {rejected.map((item, index) => (
            <li key={`${item.id ?? index}`}>
              <Mono>{item.id ?? item.type ?? "object"}</Mono> {item.reason ? `: ${item.reason}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      <Button type="submit" variant="primary" busy={saving} disabled={saving}>
        Import bundle
      </Button>
      {result ? (
        <Notice tone={result.already_imported ? "neutral" : "ok"} title={result.already_imported ? "This bundle was imported before" : "Bundle imported"}>
          <div className="space-y-1">
            <p>
              {plural(result.objects, "object")} read. Created: {countsText(result.created)}. Reused: {countsText(result.reused)}.
              {Object.keys(result.skipped).length ? ` Skipped: ${countsText(result.skipped)}.` : ""}
            </p>
            <p>Imported records are labelled as imported, not verified evidence; relationships from the bundle start unreviewed.</p>
            {result.warnings.map((warning) => (
              <p key={warning} className="text-warn">
                {warning}
              </p>
            ))}
            {result.evidence_id ? (
              <Link href={`${base}/evidence/${result.evidence_id}`} className="font-medium text-accent hover:underline">
                Open the stored bundle
              </Link>
            ) : null}
          </div>
        </Notice>
      ) : null}
    </form>
  );
}
