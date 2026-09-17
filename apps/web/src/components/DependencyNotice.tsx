import { CircleX } from "lucide-react";

import type { Readiness } from "@/lib/api-types";
import { CHECK_LABELS, CHECK_STATUS_LABELS } from "@/lib/messages";

/**
 * Explains unavailable dependencies before sign-in. Renders nothing when everything is ready.
 * `readiness === null` means the API itself could not be reached.
 */
export function DependencyNotice({ readiness }: { readiness: Readiness | null }) {
  if (readiness?.status === "ready") return null;

  return (
    <section
      role="alert"
      aria-labelledby="dependency-notice-title"
      className="mb-6 flex gap-2.5 rounded-md border border-bad-line bg-bad-soft px-3 py-3 text-sm text-ink"
    >
      <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-bad" />
      <div className="min-w-0">
        <h2 id="dependency-notice-title" className="font-semibold text-bad">
          {readiness === null ? "The API is not reachable" : "Some required services are unavailable"}
        </h2>
        {readiness === null ? (
          <p className="mt-1">
            The web interface could not contact the Tracehollow API. Check that the <code className="font-mono text-code">api</code> service
            is running, for example with <code className="font-mono text-code">docker compose ps</code>.
          </p>
        ) : (
          <ul className="mt-1.5 space-y-1">
            {Object.entries(readiness.checks)
              .filter(([, status]) => status !== "ok")
              .map(([name, status]) => (
                <li key={name}>
                  <span className="font-medium">{CHECK_LABELS[name] ?? name}:</span> {CHECK_STATUS_LABELS[status] ?? status}
                </li>
              ))}
          </ul>
        )}
      </div>
    </section>
  );
}
