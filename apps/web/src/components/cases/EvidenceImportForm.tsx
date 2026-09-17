"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useSession } from "@/lib/session-context";
import type { ImportResult } from "@/lib/workspace-types";

import { Button, Field, FormError, Notice, Select, TextArea, TextInput } from "../ui";

export const MAX_IMPORT_BYTES = 5 * 1024 * 1024;

export function buildImportForm(input: {
  kind: string;
  file: File | null;
  pasted: string;
  filename: string;
  title: string;
  importOrigin: string;
  sourceReference: string;
  publishedAt: string;
}): { form: FormData | null; problem: string | null } {
  const blob = input.file ?? (input.pasted ? new Blob([input.pasted], { type: "text/plain" }) : null);
  if (!blob) return { form: null, problem: "Choose a file or paste content to import." };
  if (blob.size > MAX_IMPORT_BYTES) return { form: null, problem: "The content is larger than the 5 MiB import limit." };
  if (input.importOrigin.trim().length < 3) {
    return { form: null, problem: "Describe where this material came from (import origin)." };
  }
  const form = new FormData();
  const name = input.file?.name ?? (input.filename.trim() || (input.kind === "json" ? "pasted.json" : "pasted.txt"));
  form.set("file", blob, name);
  form.set("kind", input.kind);
  form.set("import_origin", input.importOrigin.trim());
  if (input.title.trim()) form.set("title", input.title.trim());
  if (input.sourceReference.trim()) form.set("source_reference", input.sourceReference.trim());
  if (input.publishedAt) form.set("source_published_at", new Date(input.publishedAt).toISOString());
  return { form, problem: null };
}

export function EvidenceImportForm({ apiBase, base, onImported }: { apiBase: string; base: string; onImported: () => void }) {
  const { mutate } = useSession();
  const [kind, setKind] = useState("text");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const values = new FormData(formElement);
    const { form, problem } = buildImportForm({
      kind,
      file,
      pasted,
      filename: String(values.get("filename") ?? ""),
      title: String(values.get("title") ?? ""),
      importOrigin: String(values.get("import_origin") ?? ""),
      sourceReference: String(values.get("source_reference") ?? ""),
      publishedAt: String(values.get("published_at") ?? ""),
    });
    setResult(null);
    if (problem || !form) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const imported = await mutate<ImportResult>(`${apiBase}/evidence/imports`, { form });
      setResult(imported);
      formElement.reset();
      setFile(null);
      setPasted("");
      onImported();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5" aria-label="Import text or JSON">
      <FormError message={error} />
      {result ? (
        <Notice tone="ok" live>
          Imported{" "}
          <Link href={`${base}/evidence/${result.evidence.id}`} className="font-medium underline">
            {result.evidence.title}
          </Link>{" "}
          (SHA-256 {result.evidence.sha256.slice(0, 12)}…).
          {result.filename_sanitized ? " The filename was reduced to a safe display name." : ""}
          {result.duplicate_of.length > 0
            ? ` Identical content was imported before (${result.duplicate_of.length} record${result.duplicate_of.length === 1 ? "" : "s"}); a new record was still created.`
            : ""}
        </Notice>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Content kind" htmlFor="import-kind" hint="JSON must parse; text must be UTF-8.">
          <Select id="import-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="text">Text</option>
            <option value="json">JSON</option>
          </Select>
        </Field>
        <Field label="File" htmlFor="import-file" hint="Leave empty to paste content instead.">
          <TextInput
            id="import-file"
            type="file"
            accept={kind === "json" ? ".json,application/json" : ".txt,.md,.csv,.log,text/plain"}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>
      </div>
      {!file ? (
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="Paste content" htmlFor="import-paste" className="md:col-span-2">
            <TextArea id="import-paste" value={pasted} onChange={(event) => setPasted(event.target.value)} rows={5} className="font-mono text-code" />
          </Field>
          <Field label="Name for pasted content" htmlFor="import-filename" hint="Used as the file name of the stored original.">
            <TextInput id="import-filename" name="filename" maxLength={255} />
          </Field>
        </div>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        <Field
          label="Import origin (required)"
          htmlFor="import-origin"
          hint="Who provided it, how it was obtained and your authorization."
          className="md:col-span-2"
        >
          <TextInput id="import-origin" name="import_origin" required minLength={3} maxLength={2000} />
        </Field>
        <Field label="Title" htmlFor="import-title" hint="Defaults to the file name.">
          <TextInput id="import-title" name="title" maxLength={300} />
        </Field>
        <Field label="Source reference" htmlFor="import-reference" hint="Recorded only; Tracehollow does not fetch it.">
          <TextInput id="import-reference" name="source_reference" maxLength={2048} />
        </Field>
        <Field label="Source published (your local time)" htmlFor="import-published" hint="Stored in UTC with the value as entered.">
          <TextInput id="import-published" name="published_at" type="datetime-local" />
        </Field>
      </div>
      <Button type="submit" variant="primary" disabled={saving} busy={saving}>
        {saving ? "Importing…" : "Import evidence"}
      </Button>
    </form>
  );
}
