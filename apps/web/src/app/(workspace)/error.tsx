"use client";

import { RotateCw } from "lucide-react";

import { Button, ButtonLink, EmptyState, PageHeader } from "@/components/ui";

/** Unexpected rendering errors inside the workspace. Records are unaffected; the page can be retried. */
export default function WorkspaceError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <main id="main" className="mx-auto w-full max-w-3xl space-y-6 px-4 py-10">
      <PageHeader title="This page could not be shown" />
      <EmptyState
        title="Something went wrong while displaying this page"
        action={
          <>
            <Button variant="primary" icon={RotateCw} onClick={() => retry()}>
              Try again
            </Button>
            <ButtonLink href="/overview">Go to overview</ButtonLink>
          </>
        }
      >
        <p role="alert">
          Nothing was changed. If it happens again, check Environment status and the web service logs
          {error.digest ? (
            <>
              {" "}
              (reference <code className="font-mono text-code">{error.digest}</code>)
            </>
          ) : null}
          .
        </p>
      </EmptyState>
    </main>
  );
}
