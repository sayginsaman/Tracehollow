"use client";

import { StickyNote } from "lucide-react";
import { useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Note, Page } from "@/lib/workspace-types";

import { ActionError, Button, ConfirmAction, ErrorNotice, LoadingState, Panel, TextArea, Timestamp } from "../ui";
import { useCase } from "./CaseContext";

type Subject = { entity_id?: string; relationship_id?: string; evidence_id?: string };

export function NotesPanel({ subject = {}, title = "Analyst notes" }: { subject?: Subject; title?: string }) {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const params = new URLSearchParams({ limit: "50" });
  const subjectEntries = Object.entries(subject).filter(([, value]) => Boolean(value));
  if (subjectEntries.length === 0) params.set("case_level", "true");
  for (const [key, value] of subjectEntries) params.set(key, value as string);
  const notes = useResource<Page<Note>>(`${apiBase}/notes?${params.toString()}`);
  const inputId = `note-${subjectEntries.map(([, value]) => value).join("-") || "case"}`;

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await mutate(`${apiBase}/notes`, { body: { body: draft, ...subject } });
      setDraft("");
      await notes.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    setError(null);
    setRemoving(id);
    try {
      await mutate(`${apiBase}/notes/${id}`, { method: "DELETE" });
      await notes.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setRemoving(null);
    }
  }

  return (
    <Panel title={title} description="Interpretation is recorded separately from source observations and evidence.">
      <div className="space-y-4">
        <ActionError message={error} />
        {notes.state === "error" ? <ErrorNotice error={notes.error} onRetry={() => void notes.reload()} /> : null}
        {notes.state === "loading" && !notes.data ? <LoadingState label="Loading notes…" rows={2} /> : null}
        {notes.data && notes.data.items.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted">
            <StickyNote aria-hidden="true" className="size-4 shrink-0" />
            No notes yet.{writable ? " Add an interpretation, caveat or follow-up below." : ""}
          </p>
        ) : null}
        {notes.data && notes.data.items.length > 0 ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {notes.data.items.map((note) => (
              <li key={note.id} className="px-3 py-3">
                <p className="max-w-[72ch] text-read whitespace-pre-wrap break-words text-ink">{note.body}</p>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
                  <span>
                    Added <Timestamp value={note.created_at} />
                  </span>
                  {writable ? (
                    <ConfirmAction
                      label="Delete note"
                      confirmLabel="Delete"
                      message="Delete this note?"
                      busy={removing === note.id}
                      onConfirm={() => void remove(note.id)}
                    />
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
        {writable ? (
          <form onSubmit={add} className="space-y-2">
            <label htmlFor={inputId} className="sr-only">
              New note
            </label>
            <TextArea
              id={inputId}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={3}
              maxLength={20000}
              placeholder="Add an interpretation, caveat or follow-up…"
              required
            />
            <Button type="submit" disabled={saving || !draft.trim()} busy={saving}>
              {saving ? "Saving…" : "Add note"}
            </Button>
          </form>
        ) : null}
      </div>
    </Panel>
  );
}
