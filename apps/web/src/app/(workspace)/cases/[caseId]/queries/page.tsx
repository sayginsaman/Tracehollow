import type { Metadata } from "next";

import { QueriesView } from "@/components/cases/QueriesView";

export const metadata: Metadata = { title: "Queries & runs" };

export default async function QueriesPage({ searchParams }: PageProps<"/cases/[caseId]/queries">) {
  const { new: create } = await searchParams;
  return <QueriesView startCreating={create === "1"} />;
}
