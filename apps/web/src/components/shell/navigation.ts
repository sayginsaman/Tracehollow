import {
  Activity,
  Boxes,
  CalendarClock,
  FileOutput,
  FileUp,
  Files,
  FolderOpen,
  GitCompareArrows,
  House,
  LayoutDashboard,
  ListChecks,
  MessageSquareQuote,
  Network,
  Plug,
  Settings2,
  SlidersHorizontal,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Extra path prefixes that also mark this item as current. */
  also?: string[];
  exact?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const WORKSPACE_NAV: NavGroup = {
  label: "Workspace",
  items: [
    { label: "Overview", href: "/overview", icon: House },
    { label: "Cases", href: "/cases", icon: FolderOpen, exact: true },
  ],
};

export const CONFIGURATION_NAV: NavGroup = {
  label: "Configuration",
  items: [
    { label: "Sources", href: "/sources", icon: Plug },
    { label: "Environment status", href: "/status", icon: Activity },
    { label: "Preferences", href: "/preferences", icon: SlidersHorizontal },
  ],
};

/** Case sections, grouped by the task they serve, in workflow order. */
export function caseNavigation(base: string): NavGroup[] {
  return [
    { label: "", items: [{ label: "Case overview", href: base, icon: LayoutDashboard, exact: true }] },
    {
      label: "Collect",
      items: [
        { label: "Queries & runs", href: `${base}/queries`, icon: ListChecks, also: [`${base}/runs`] },
        { label: "Imports", href: `${base}/imports`, icon: FileUp },
      ],
    },
    {
      label: "Examine",
      items: [
        { label: "Evidence", href: `${base}/evidence`, icon: Files },
        { label: "Entities", href: `${base}/entities`, icon: Boxes },
        { label: "Relationships", href: `${base}/relationships`, icon: Waypoints },
        { label: "Graph", href: `${base}/graph`, icon: Network },
      ],
    },
    {
      label: "Analyze",
      items: [
        { label: "Timeline", href: `${base}/timeline`, icon: CalendarClock },
        { label: "Compare", href: `${base}/compare`, icon: GitCompareArrows },
        { label: "AI", href: `${base}/ai`, icon: MessageSquareQuote },
      ],
    },
    {
      label: "Report",
      items: [
        { label: "Reports", href: `${base}/reports`, icon: FileOutput },
        { label: "Case settings", href: `${base}/settings`, icon: Settings2 },
      ],
    },
  ];
}

export function isCurrent(item: NavItem, pathname: string): boolean {
  if (item.exact) return pathname === item.href;
  return [item.href, ...(item.also ?? [])].some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

const SECTION_LABELS: Record<string, string> = {
  queries: "Queries & runs",
  runs: "Queries & runs",
  imports: "Imports",
  evidence: "Evidence",
  entities: "Entities",
  relationships: "Relationships",
  graph: "Graph",
  timeline: "Timeline",
  compare: "Compare",
  ai: "AI",
  reports: "Reports",
  settings: "Case settings",
};

const GLOBAL_LABELS: Record<string, string> = {
  overview: "Overview",
  cases: "Cases",
  sources: "Sources",
  status: "Environment status",
  preferences: "Preferences",
};

const DETAIL_FALLBACK: Record<string, string> = {
  runs: "Run",
  evidence: "Evidence record",
  entities: "Entity",
  ai: "Conversation",
};

export interface Crumb {
  label: string;
  href?: string;
}

/** Breadcrumbs for a path; `caseTitle` names the case and `leaf` names a detail record. */
export function breadcrumbs(pathname: string, caseTitle: string | null, leaf: string | null): Crumb[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "cases" || parts.length < 2) {
    const label = GLOBAL_LABELS[parts[0] ?? "overview"];
    return label ? [{ label }] : [];
  }
  const base = `/cases/${parts[1]}`;
  const crumbs: Crumb[] = [{ label: "Cases", href: "/cases" }, { label: caseTitle ?? "Case", href: base }];
  const section = parts[2];
  if (!section) return crumbs.map((crumb, index) => (index === crumbs.length - 1 ? { label: crumb.label } : crumb));
  const sectionHref = section === "runs" ? `${base}/queries` : `${base}/${section}`;
  const detail = parts.length > 3;
  crumbs.push({ label: SECTION_LABELS[section] ?? section, href: detail ? sectionHref : undefined });
  if (detail) crumbs.push({ label: leaf ?? DETAIL_FALLBACK[section] ?? "Details" });
  return crumbs;
}
