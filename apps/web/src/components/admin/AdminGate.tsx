"use client";

import type { ReactNode } from "react";

import { hasSystemPermission, type SystemPermission } from "@/lib/permissions";
import { useSession } from "@/lib/session-context";

import { Notice } from "../ui";

/** Renders administration pages only for accounts with the permission; the API enforces it regardless. */
export function AdminGate({ permission, children }: { permission: SystemPermission; children: ReactNode }) {
  const { session } = useSession();
  if (!hasSystemPermission(session, permission)) {
    return <Notice tone="neutral" title="Administrator access required">This page is available to administrator accounts.</Notice>;
  }
  return <>{children}</>;
}
