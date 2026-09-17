import { ShieldCheck } from "lucide-react";

/** Centered layout for first-run setup, sign-in and startup problems (no session, no sidebar). */
export function AuthShell({
  title,
  description,
  children,
}: {
  title: string;
  description: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <main id="main" tabIndex={-1} className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-center px-4 py-12 outline-none">
      <div className="mb-8 flex items-center gap-2.5">
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-7">
          <rect x="1.5" y="1.5" width="21" height="21" rx="5" className="fill-accent" />
          <circle cx="12" cy="12" r="5.25" fill="none" strokeWidth="2" className="stroke-on-accent" />
          <path d="M4.5 16.5 9 12.5" strokeWidth="2" strokeLinecap="round" className="stroke-on-accent" />
        </svg>
        <div>
          <p className="font-semibold tracking-tight text-ink">Tracehollow</p>
          <p className="text-xs text-muted">Local-first investigation workspace</p>
        </div>
      </div>
      <div className="rounded-lg border border-line bg-surface p-6 sm:p-8">
        <h1 className="text-title font-semibold text-ink">{title}</h1>
        <div className="mt-2 text-sm text-muted">{description}</div>
        <div className="mt-6">{children}</div>
      </div>
      <p className="mt-6 flex items-start gap-2 text-xs text-muted">
        <ShieldCheck aria-hidden="true" className="mt-px size-4 shrink-0" />
        Sign-in is required on every installation, including localhost. Sessions expire after inactivity.
      </p>
    </main>
  );
}
