"use client";

import { Archive, ArchiveRestore, Download, FileJson, FileSpreadsheet, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useSession } from "@/lib/session-context";
import type { CaseDeletion, CaseDetail } from "@/lib/workspace-types";

import { ActionError, Button, ButtonLink, Field, PageHeader, Panel, SubHeading, TextInput, Timestamp } from "../ui";
import { BudgetsPanel, RetentionPanel, StixExportPanel, ViewerExportNotice } from "./CasePolicies";
import { useCase } from "./CaseContext";
import { CaseStatusBadge } from "./case-status";

export function CaseSettings() {
  const { caseDetail, apiBase, setCase, can } = useCase();
  const analyst = can("case.edit");
  const { mutate } = useSession();
  const router = useRouter();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"lifecycle" | "delete" | null>(null);

  async function changeLifecycle(action: "archive" | "restore") {
    setBusy("lifecycle");
    setLifecycleError(null);
    try {
      setCase(await mutate<CaseDetail>(`${apiBase}/${action}`));
    } catch (caught) {
      setLifecycleError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function requestDeletion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("delete");
    setError(null);
    try {
      await mutate<CaseDeletion>(`${apiBase}/deletion`, { body: { confirm_title: confirmation } });
      router.push("/cases?deletion=requested");
      router.refresh();
    } catch (caught) {
      setError(describeError(caught));
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Case settings" description="Status, collection budget, retention, exports and deletion for this case. Case details are edited on the case overview." />

      <Panel title="Case status">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-[72ch] space-y-1 text-sm">
            <p className="flex items-center gap-2">
              <span className="text-muted">Current status</span> <CaseStatusBadge status={caseDetail.status} />
            </p>
            {caseDetail.status === "archived" ? (
              <p className="text-muted">
                Archived <Timestamp value={caseDetail.archived_at} />. Everything stays readable and exportable; nothing can be changed,
                collected or imported until the case is restored.
              </p>
            ) : (
              <p className="text-muted">
                Archiving makes the case read-only without deleting anything. Wait for queued or running executions to finish first.
              </p>
            )}
          </div>
          {caseDetail.status === "active" && analyst ? (
            <Button icon={Archive} onClick={() => void changeLifecycle("archive")} disabled={busy !== null} busy={busy === "lifecycle"}>
              Archive case
            </Button>
          ) : null}
          {caseDetail.status === "archived" && analyst ? (
            <Button variant="primary" icon={ArchiveRestore} onClick={() => void changeLifecycle("restore")} disabled={busy !== null} busy={busy === "lifecycle"}>
              Restore case
            </Button>
          ) : null}
        </div>
        <ActionError message={lifecycleError} className="mt-3" />
      </Panel>

      <BudgetsPanel />

      <RetentionPanel />

      {can("exports.create") ? (
        <Panel title="Export" description="Exports contain case content with provenance references and a manifest. Treat downloaded files as sensitive.">
          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <SubHeading className="flex items-center gap-2">
                <FileJson aria-hidden="true" className="size-4 text-muted" />
                JSON
              </SubHeading>
              <p className="text-sm text-muted">
                One document with a manifest (record counts, source dates, coverage gaps, acquisition methods and a data hash) and every
                case record.
              </p>
              <ButtonLink href={`${apiBase}/exports/json`} download external variant="primary" icon={Download}>
                Download JSON export
              </ButtonLink>
            </div>
            <div className="space-y-2">
              <SubHeading className="flex items-center gap-2">
                <FileSpreadsheet aria-hidden="true" className="size-4 text-muted" />
                CSV (ZIP)
              </SubHeading>
              <p className="text-sm text-muted">
                One UTF-8 CSV per record type plus manifest.json with a SHA-256 for each file. Cells that could run as spreadsheet formulas
                are neutralized.
              </p>
              <ButtonLink href={`${apiBase}/exports/csv`} download external icon={Download}>
                Download CSV export
              </ButtonLink>
            </div>
          </div>
          <ul className="mt-5 list-inside list-disc space-y-1 border-t border-line pt-4 text-xs text-muted">
            <li>Never included: passwords, sessions, tokens, application secrets, internal storage paths.</li>
            <li>Original evidence bytes are referenced by ID and SHA-256; download them from each evidence page.</li>
            <li>Exports are not redacted. Build a report for a redacted, shareable file. Synthetic fixture records are flagged in the manifest.</li>
          </ul>
          <div className="mt-5 border-t border-line pt-5">
            <StixExportPanel />
          </div>
        </Panel>
      ) : (
        <ViewerExportNotice />
      )}

      {can("case.delete") ? (
        <Panel title="Delete case" description="Deletion is permanent for this installation and cannot be undone from the application." className="border-bad-line">
          <div className="max-w-[72ch] space-y-2 text-sm">
            <p className="text-ink">
              Deleting removes the case, its entities, relationships, notes, saved queries, executions, observations, evidence records and all
              stored evidence files. A deletion job reports progress and can be retried if it fails.
            </p>
            <p className="text-muted">
              Monitors stop at once. Copies that already exist outside the application are not affected: previous backups made with
              scripts/backup.sh, export files and reports you downloaded, and webhook notifications already delivered keep their contents.
            </p>
          </div>
          <form onSubmit={requestDeletion} className="mt-4 max-w-xl space-y-3">
            <ActionError message={error} />
            <Field label={`Type the case title to confirm: ${caseDetail.title}`} htmlFor="confirm-title">
              <TextInput id="confirm-title" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" />
            </Field>
            <Button
              type="submit"
              variant="danger"
              icon={Trash2}
              disabled={busy !== null || confirmation !== caseDetail.title}
              busy={busy === "delete"}
            >
              {busy === "delete" ? "Requesting deletion…" : "Delete this case"}
            </Button>
          </form>
        </Panel>
      ) : null}
    </div>
  );
}
