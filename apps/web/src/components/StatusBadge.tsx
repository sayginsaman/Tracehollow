type Tone = "ok" | "warn" | "bad" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-ok-bg text-ok border-ok/30",
  warn: "bg-warn-bg text-warn border-warn/30",
  bad: "bg-bad-bg text-bad border-bad/30",
  neutral: "bg-canvas text-muted border-line",
};

// A glyph accompanies the label so status is never conveyed by colour alone.
const TONE_GLYPHS: Record<Tone, string> = {
  ok: "✓",
  warn: "!",
  bad: "✕",
  neutral: "…",
};

export function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      <span aria-hidden="true">{TONE_GLYPHS[tone]}</span>
      {label}
    </span>
  );
}

export type { Tone };
