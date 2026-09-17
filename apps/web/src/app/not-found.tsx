import type { Metadata } from "next";

import { AuthShell } from "@/components/AuthShell";
import { ButtonLink } from "@/components/ui";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <AuthShell
      title="Not found"
      description="This page or record does not exist, or you do not have access to it. Access-controlled records look the same as missing ones."
    >
      <div className="flex flex-wrap gap-2">
        <ButtonLink href="/overview" variant="primary">
          Go to overview
        </ButtonLink>
        <ButtonLink href="/cases">Open cases</ButtonLink>
      </div>
    </AuthShell>
  );
}
