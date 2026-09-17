"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useCase } from "./CaseContext";
import { caseStatusBadge } from "./CaseList";

const TABS = [
  { segment: "", label: "Overview" },
  { segment: "/entities", label: "Entities" },
  { segment: "/relationships", label: "Relationships" },
  { segment: "/evidence", label: "Evidence" },
  { segment: "/queries", label: "Queries & runs" },
  { segment: "/graph", label: "Graph" },
  { segment: "/timeline", label: "Timeline" },
  { segment: "/compare", label: "Compare" },
  { segment: "/ai", label: "AI" },
  { segment: "/reports", label: "Reports" },
  { segment: "/settings", label: "Export & delete" },
];

export function CaseHeader() {
  const { caseDetail, base } = useCase();
  const pathname = usePathname();

  function isActive(segment: string): boolean {
    if (segment === "") return pathname === base;
    if (segment === "/queries") return pathname.startsWith(`${base}/queries`) || pathname.startsWith(`${base}/runs`);
    return pathname.startsWith(`${base}${segment}`);
  }

  return (
    <div className="space-y-3">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/cases" className="hover:underline">
          Cases
        </Link>{" "}
        / <span className="text-ink">{caseDetail.title}</span>
      </nav>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{caseDetail.title}</h1>
        {caseStatusBadge(caseDetail.status)}
        {caseDetail.tags.map((tag) => (
          <span key={tag} className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">
            {tag}
          </span>
        ))}
      </div>
      {caseDetail.status === "archived" ? (
        <p role="status" className="rounded-md border border-line bg-canvas px-3 py-2 text-sm text-muted">
          This case is archived and read-only. Restore it from the overview to make changes.
        </p>
      ) : null}
      <nav aria-label="Case sections" className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map((tab) => {
          const active = isActive(tab.segment);
          return (
            <Link
              key={tab.label}
              href={`${base}${tab.segment}`}
              aria-current={active ? "page" : undefined}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                active ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
