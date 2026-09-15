"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useSession } from "@/lib/session-context";
import type { CaseDeletion } from "@/lib/workspace-types";

import { Button, Field, Section, TextInput } from "../ui";
import { useCase } from "./CaseContext";

export function CaseSettings() {
  const { caseDetail, apiBase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function requestDeletion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await mutate<CaseDeletion>(`${apiBase}/deletion`, { body: { confirm_title: confirmation } });
      router.push("/cases?deletion=requested");
      router.refresh();
    } catch (caught) {
      setError(describeError(caught));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Section
        title="Export"
        description="Exports contain case content with provenance references and a manifest. Treat downloaded files as sensitive."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2 text-sm">
            <h3 className="font-medium">JSON</h3>
            <p className="text-muted">
              One document with a manifest (record counts, source dates, coverage gaps, acquisition methods and a data
              hash) and every case record.
            </p>
            <a
              href={`${apiBase}/exports/json`}
              download
              className="inline-flex items-center rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:bg-accent-strong dark:text-canvas"
            >
              Download JSON export
            </a>
          </div>
          <div className="space-y-2 text-sm">
            <h3 className="font-medium">CSV (ZIP)</h3>
            <p className="text-muted">
              One UTF-8 CSV per record type plus manifest.json with a SHA-256 for each file. Cells that could run as
              spreadsheet formulas are neutralized.
            </p>
            <a
              href={`${apiBase}/exports/csv`}
              download
              className="inline-flex items-center rounded-md border border-line px-3 py-1.5 font-medium hover:bg-canvas"
            >
              Download CSV export
            </a>
          </div>
        </div>
        <ul className="mt-4 list-inside list-disc text-xs text-muted">
          <li>Never included: passwords, sessions, tokens, application secrets, internal storage paths.</li>
          <li>Original evidence bytes are referenced by ID and SHA-256; download them from each evidence page.</li>
          <li>No redaction is applied in this version. Synthetic fixture records are flagged in the manifest.</li>
        </ul>
      </Section>

      <Section title="Delete case" description="Deletion is permanent for this installation and cannot be undone from the application.">
        <div className="space-y-2 text-sm">
          <p>
            Deleting removes the case, its entities, relationships, notes, saved queries, executions, observations,
            evidence records and all stored evidence files. A deletion job reports progress and can be retried if it
            fails.
          </p>
          <p className="text-muted">
            Copies that already exist outside the application are not affected: previous backups made with
            scripts/backup.sh and export files you downloaded keep their contents until you delete them yourself.
          </p>
        </div>
        <form onSubmit={requestDeletion} className="mt-4 space-y-3">
          {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
          <Field label={`Type the case title to confirm: ${caseDetail.title}`} htmlFor="confirm-title">
            <TextInput
              id="confirm-title"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Button type="submit" variant="danger" disabled={busy || confirmation !== caseDetail.title} aria-busy={busy}>
            {busy ? "Requesting deletion…" : "Delete this case"}
          </Button>
        </form>
      </Section>
    </div>
  );
}
