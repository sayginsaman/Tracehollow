"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { apiRequest } from "@/lib/client-api";
import { caseCan, type CasePermission } from "@/lib/permissions";
import { useSession } from "@/lib/session-context";
import type { CaseDetail } from "@/lib/workspace-types";

interface CaseContextValue {
  caseDetail: CaseDetail;
  base: string;
  apiBase: string;
  /** The case is active and the signed-in account may change it (analyst role). */
  writable: boolean;
  /** Effective role in this case. */
  role: "analyst" | "viewer";
  can: (permission: CasePermission) => boolean;
  refreshCase: () => Promise<void>;
  setCase: (value: CaseDetail) => void;
}

const CaseContext = createContext<CaseContextValue | null>(null);

export function CaseProvider({ initialCase, children }: { initialCase: CaseDetail; children: ReactNode }) {
  const [caseDetail, setCase] = useState(initialCase);
  const { handleAuthError } = useSession();

  const refreshCase = useCallback(async () => {
    try {
      setCase(await apiRequest<CaseDetail>(`/api/v1/cases/${initialCase.id}`));
    } catch (error) {
      handleAuthError(error);
    }
  }, [initialCase.id, handleAuthError]);

  return (
    <CaseContext.Provider
      value={{
        caseDetail,
        base: `/cases/${caseDetail.id}`,
        apiBase: `/api/v1/cases/${caseDetail.id}`,
        writable: caseDetail.status === "active" && caseCan(caseDetail, "case.edit"),
        role: caseDetail.my_role === "viewer" ? "viewer" : "analyst",
        can: (permission: CasePermission) => caseCan(caseDetail, permission),
        refreshCase,
        setCase,
      }}
    >
      {children}
    </CaseContext.Provider>
  );
}

/** The current case when inside one (the application shell renders on pages outside cases too). */
export function useOptionalCase(): CaseContextValue | null {
  return useContext(CaseContext);
}

export function useCase(): CaseContextValue {
  const value = useContext(CaseContext);
  if (value === null) throw new Error("useCase must be used inside CaseProvider");
  return value;
}
