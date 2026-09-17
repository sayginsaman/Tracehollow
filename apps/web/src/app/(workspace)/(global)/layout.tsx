import { AppShell } from "@/components/shell/AppShell";

/** Pages outside a case: overview, case list, sources, environment status and preferences. */
export default function GlobalLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
