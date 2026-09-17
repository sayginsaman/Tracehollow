"use client";

import { createContext, useContext, useEffect } from "react";

interface ShellContextValue {
  setLeaf: (label: string | null) => void;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

/** Names the current detail record in the breadcrumbs (for example an evidence title). */
export function usePageCrumb(label: string | null | undefined): void {
  const shell = useContext(ShellContext);
  useEffect(() => {
    if (!shell || !label) return;
    shell.setLeaf(label);
    return () => shell.setLeaf(null);
  }, [shell, label]);
}
