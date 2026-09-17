"use client";

import { Archive } from "lucide-react";
import Link from "next/link";

import { Notice } from "../ui";
import { useCase } from "./CaseContext";

/** Shown on every page of an archived case, which is read-only until restored. */
export function ArchivedCaseNotice() {
  const { caseDetail, base, can } = useCase();
  if (caseDetail.status !== "archived") return null;
  if (!can("case.edit")) {
    return (
      <Notice icon={Archive} className="mb-6">
        This case is archived and read-only. An analyst of the case can restore it.
      </Notice>
    );
  }
  return (
    <Notice icon={Archive} className="mb-6">
      This case is archived and read-only. Restore it in{" "}
      <Link href={`${base}/settings`} className="font-medium text-accent underline">
        Case settings
      </Link>{" "}
      to make changes.
    </Notice>
  );
}
