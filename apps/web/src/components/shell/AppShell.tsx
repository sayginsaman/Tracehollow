"use client";

import { Bell, ChevronRight, Eye, FlaskConical, LogOut, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";
import { ROLE_LABELS, caseCan, hasSystemPermission } from "@/lib/permissions";
import { useSession } from "@/lib/session-context";

import { useOptionalCase } from "../cases/CaseContext";
import { CaseStatusBadge, SYNTHETIC_DEMO_TAG } from "../cases/case-status";
import { ActionError, Button, StatusBadge, cn } from "../ui";
import { ADMINISTRATION_NAV, CONFIGURATION_NAV, WORKSPACE_NAV, breadcrumbs, caseNavigation, isCurrent, type NavGroup } from "./navigation";
import { ShellContext } from "./ShellContext";

function BrandMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-6 shrink-0">
      <rect x="1.5" y="1.5" width="21" height="21" rx="5" className="fill-accent" />
      <circle cx="12" cy="12" r="5.25" fill="none" strokeWidth="2" className="stroke-on-accent" />
      <path d="M4.5 16.5 9 12.5" strokeWidth="2" strokeLinecap="round" className="stroke-on-accent" />
    </svg>
  );
}

const UNREAD_POLL_MS = 60_000;

/** Unread notification count, refreshed on navigation and every minute. */
function useUnreadCount(pathname: string): number | null {
  const [unread, setUnread] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await apiRequest<{ unread: number }>("/api/v1/notifications/unread-count");
        if (!cancelled) setUnread(result.unread);
      } catch {
        if (!cancelled) setUnread(null);
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), UNREAD_POLL_MS);
    const changed = () => void load();
    window.addEventListener("tracehollow:notifications-changed", changed);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("tracehollow:notifications-changed", changed);
    };
  }, [pathname]);
  return unread;
}

function NavSection({ group, pathname, labelId }: { group: NavGroup; pathname: string; labelId: string }) {
  return (
    <div className="mt-4 first:mt-0">
      {group.label ? (
        <p id={labelId} className="px-2.5 pb-1 text-xs font-medium text-muted">
          {group.label}
        </p>
      ) : null}
      <ul aria-labelledby={group.label ? labelId : undefined} className="space-y-px">
        {group.items.map((item) => {
          const current = isCurrent(item, pathname);
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
                  current ? "bg-accent-soft font-medium text-accent-strong" : "text-muted hover:bg-line/50 hover:text-ink",
                )}
              >
                <Icon aria-hidden="true" className="size-4 shrink-0" />
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { session, mutate } = useSession();
  const currentCase = useOptionalCase();
  const pathname = usePathname();
  const router = useRouter();
  // The drawer is open for the page it was opened on, so navigating closes it.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === pathname;
  const [leaf, setLeaf] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => {
    setOpenOn(null);
    // Wait for the content to stop being inert before returning focus to the menu button.
    window.requestAnimationFrame(() => menuButton.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    const focusDrawer = () => {
      const drawer = document.getElementById("app-sidebar");
      if (drawer && !drawer.contains(document.activeElement)) closeButton.current?.focus();
    };
    const frame = window.requestAnimationFrame(focusDrawer);
    // The content behind the drawer becomes inert in the same update and the browser may drop focus afterwards.
    const retry = window.setTimeout(focusDrawer, 120);
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(retry);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);

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

  const shell = useMemo(() => ({ setLeaf }), []);
  const unread = useUnreadCount(pathname);
  const administrator = hasSystemPermission(session, "accounts.manage");
  const crumbs = breadcrumbs(pathname, currentCase?.caseDetail.title ?? null, leaf);
  const caseDetail = currentCase?.caseDetail;

  return (
    <ShellContext.Provider value={shell}>
      {/* The column background continues the sidebar below the fold when the page is taller than the viewport. */}
      <div className="min-h-dvh lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)] lg:bg-[linear-gradient(to_right,var(--color-sidebar)_calc(15.5rem-1px),var(--color-line)_calc(15.5rem-1px)_15.5rem,transparent_15.5rem)]">
        {open ? (
          // Pointer-only backdrop; keyboard and screen reader users close the drawer with Escape or its close button.
          <button
            type="button"
            aria-hidden="true"
            tabIndex={-1}
            onClick={close}
            className="fixed inset-0 z-(--z-backdrop) bg-backdrop lg:hidden"
          />
        ) : null}

        <aside
          id="app-sidebar"
          aria-label="Sidebar"
          className={cn(
            "fixed inset-y-0 left-0 z-(--z-drawer) flex w-72 max-w-[85vw] flex-col border-r border-line bg-sidebar duration-200 ease-out-quart",
            "lg:sticky lg:top-0 lg:z-auto lg:h-dvh lg:w-auto lg:max-w-none lg:translate-x-0 lg:visible lg:transition-none",
            // Opening shows the drawer at once (so focus can move into it); closing hides it after it slides out.
            open ? "visible translate-x-0 shadow-overlay transition-[translate]" : "invisible -translate-x-full transition-[translate,visibility]",
          )}
        >
          <div className="flex h-13 shrink-0 items-center justify-between gap-2 border-b border-line px-4">
            <Link href="/overview" className="flex items-center gap-2 rounded-md font-semibold tracking-tight text-ink">
              <BrandMark />
              Tracehollow
            </Link>
            <button
              ref={closeButton}
              type="button"
              onClick={close}
              aria-label="Close navigation"
              className="inline-flex size-8 items-center justify-center rounded-md text-muted hover:bg-line/50 hover:text-ink lg:hidden"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
          </div>

          <nav aria-label="Primary" className="flex-1 overflow-y-auto overscroll-contain px-3 py-4">
            <NavSection group={WORKSPACE_NAV} pathname={pathname} labelId="nav-workspace" />

            {caseDetail && currentCase ? (
              <div role="group" aria-labelledby="nav-case-title" className="mt-5 border-t border-line pt-4">
                <div className="mb-2 px-2.5">
                  <p className="text-xs font-medium text-muted">Current case</p>
                  <p id="nav-case-title" className="mt-0.5 line-clamp-2 text-sm font-semibold break-words text-ink" title={caseDetail.title}>
                    {caseDetail.title}
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <CaseStatusBadge status={caseDetail.status} />
                    {caseDetail.tags.includes(SYNTHETIC_DEMO_TAG) ? <StatusBadge tone="warn" icon={FlaskConical} label="Synthetic demo" /> : null}
                    {caseDetail.my_role === "viewer" ? (
                      <StatusBadge tone="neutral" icon={Eye} label="View only" title="Your role in this case is viewer: read access only." />
                    ) : null}
                  </div>
                </div>
                {caseNavigation(currentCase.base, { audit: caseCan(caseDetail, "audit.case.read") }).map((group, index) => (
                  <NavSection key={group.label || "case"} group={group} pathname={pathname} labelId={`nav-case-${index}`} />
                ))}
              </div>
            ) : null}

            {administrator ? (
              <div className="mt-5 border-t border-line pt-4">
                <NavSection group={ADMINISTRATION_NAV} pathname={pathname} labelId="nav-administration" />
              </div>
            ) : null}

            <div className="mt-5 border-t border-line pt-4">
              <NavSection group={CONFIGURATION_NAV} pathname={pathname} labelId="nav-configuration" />
            </div>
          </nav>

          <div className="shrink-0 border-t border-line px-3 py-3">
            <div className="flex items-center justify-between gap-2 px-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink" title={session.user.username}>
                  {session.user.username}
                </p>
                <p className="text-xs text-muted">{ROLE_LABELS[session.user.role] ?? (session.user.is_admin ? "Administrator" : "Analyst")}</p>
              </div>
              <Button size="sm" variant="ghost" icon={LogOut} onClick={() => void signOut()} busy={signingOut} disabled={signingOut}>
                {signingOut ? "Signing out…" : "Sign out"}
              </Button>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-col" inert={open ? true : undefined}>
          <header className="sticky top-0 z-(--z-header) flex h-13 items-center gap-2 border-b border-line bg-surface px-4 sm:px-6 lg:px-8">
            <button
              ref={menuButton}
              type="button"
              onClick={() => setOpenOn(pathname)}
              aria-controls="app-sidebar"
              aria-expanded={open}
              aria-label="Open navigation"
              className="-ml-2 inline-flex size-9 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink lg:hidden"
            >
              <Menu aria-hidden="true" className="size-5" />
            </button>
            <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
              <ol className="flex min-w-0 items-center gap-1 text-sm">
                {crumbs.map((crumb, index) => {
                  const last = index === crumbs.length - 1;
                  // Small screens keep the last two crumbs; the sidebar still names the case.
                  const hiddenOnSmall = index < crumbs.length - 2;
                  return (
                    <li key={`${crumb.label}-${index}`} className={cn("flex min-w-0 items-center gap-1", hiddenOnSmall && "max-sm:hidden", !last && "max-w-[40%] shrink-0")}>
                      {index > 0 ? <ChevronRight aria-hidden="true" className={cn("size-3.5 shrink-0 text-subtle", index === crumbs.length - 2 && "max-sm:hidden")} /> : null}
                      {crumb.href && !last ? (
                        <Link href={crumb.href} className="truncate rounded text-muted hover:text-ink hover:underline" title={crumb.label}>
                          {crumb.label}
                        </Link>
                      ) : (
                        <span aria-current={last ? "page" : undefined} className={cn("truncate", last ? "font-medium text-ink" : "text-muted")} title={crumb.label}>
                          {crumb.label}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
            </nav>
            <Link
              href="/notifications"
              aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
              title={unread ? `${unread} unread notification${unread === 1 ? "" : "s"}` : "Notifications"}
              className="relative inline-flex size-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink"
            >
              <Bell aria-hidden="true" className="size-5" />
              {unread ? (
                <span
                  aria-hidden="true"
                  className="absolute top-1 right-0.5 min-w-4 rounded-full bg-accent px-1 text-center text-[0.6875rem] leading-4 font-semibold text-on-accent tabular-nums"
                >
                  {unread > 99 ? "99+" : unread}
                </span>
              ) : null}
            </Link>
          </header>

          {error ? <ActionError message={error} className="mx-4 mt-4 sm:mx-6 lg:mx-8" /> : null}

          <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[84rem] flex-1 px-4 pt-6 pb-16 outline-none sm:px-6 lg:px-8 lg:pt-8">
            {children}
          </main>
        </div>
      </div>
    </ShellContext.Provider>
  );
}
