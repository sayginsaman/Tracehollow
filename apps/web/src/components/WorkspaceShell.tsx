"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { describeError } from "@/lib/messages";
import { useSession } from "@/lib/session-context";

const NAV = [
  { href: "/cases", label: "Cases" },
  { href: "/status", label: "Environment status" },
];

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const { session, mutate } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signOut() {
    setSigningOut(true);
    setError(null);
    try {
      await mutate("/api/v1/auth/logout");
      router.replace("/login");
      router.refresh();
    } catch (caught) {
      setError(describeError(caught));
      setSigningOut(false);
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-6">
            <Link href="/cases" className="text-sm font-semibold tracking-wide">
              Tracehollow
            </Link>
            <nav aria-label="Primary" className="flex gap-1 text-sm">
              {NAV.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`rounded-md px-2.5 py-1.5 ${active ? "bg-canvas font-medium text-ink" : "text-muted hover:text-ink"}`}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted">
              Signed in as <span className="font-medium text-ink">{session.user.username}</span>
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              disabled={signingOut}
              aria-busy={signingOut}
              className="rounded-md border border-line px-3 py-1.5 font-medium hover:bg-canvas disabled:opacity-60"
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      </header>
      {error ? (
        <p role="alert" className="mx-auto mt-4 max-w-6xl rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">
          {error}
        </p>
      ) : null}
      <main id="main" className="mx-auto max-w-6xl px-4 py-6">
        {children}
      </main>
    </div>
  );
}
