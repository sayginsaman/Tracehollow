"use client";

import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Note, Page } from "@/lib/workspace-types";

import { Button, EmptyState, ErrorNotice, LoadingState, Section, TextArea } from "../ui";
import { useCase } from "./CaseContext";

type Subject = { entity_id?: string; relationship_id?: string; evidence_id?: string };

export function NotesPanel({ subject = {}, title = "Analyst notes" }: { subject?: Subject; title?: string }) {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState("");

  const params = new URLSearchParams({ limit: "50" });
  const subjectEntries = Object.entries(subject).filter(([, value]) => Boolean(value));
  if (subjectEntries.length === 0) params.set("case_level", "true");
  for (const [key, value] of subjectEntries) params.set(key, value as string);
  const notes = useResource<Page<Note>>(`${apiBase}/notes?${params.toString()}`);

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
    try {
      await mutate(`${apiBase}/notes/${id}`, { method: "DELETE" });
      await notes.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <Section title={title} description="Interpretation is recorded separately from source observations and evidence.">
      {error ? <p role="alert" className="mb-2 text-sm text-bad">{error}</p> : null}
      {notes.state === "error" ? <ErrorNotice error={notes.error} onRetry={() => void notes.reload()} /> : null}
      {notes.state === "loading" && !notes.data ? <LoadingState /> : null}
      {notes.data && notes.data.items.length === 0 ? <EmptyState>No notes yet.</EmptyState> : null}
      {notes.data && notes.data.items.length > 0 ? (
        <ul className="mb-3 space-y-2">
          {notes.data.items.map((note) => (
            <li key={note.id} className="rounded-md border border-line p-3 text-sm">
              <p className="whitespace-pre-wrap break-words">{note.body}</p>
              <div className="mt-2 flex items-center justify-between text-xs text-muted">
                <span>{formatUtc(note.created_at)}</span>
                {writable ? (
                  <button type="button" className="text-bad hover:underline" onClick={() => void remove(note.id)}>
                    Delete note
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {writable ? (
        <form onSubmit={add} className="space-y-2">
          <label htmlFor={`note-${subjectEntries.map(([, v]) => v).join("-") || "case"}`} className="sr-only">
            New note
          </label>
          <TextArea
            id={`note-${subjectEntries.map(([, v]) => v).join("-") || "case"}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            maxLength={20000}
            placeholder="Add an interpretation, caveat or follow-up…"
            required
          />
          <Button type="submit" variant="primary" disabled={saving || !draft.trim()} aria-busy={saving}>
            {saving ? "Saving…" : "Add note"}
          </Button>
        </form>
      ) : null}
    </Section>
  );
}
