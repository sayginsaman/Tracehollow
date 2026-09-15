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
      className="mb-6 rounded-md border border-bad/30 bg-bad-bg p-4 text-sm text-ink"
    >
      <h2 id="dependency-notice-title" className="font-semibold text-bad">
        {readiness === null ? "The API is not reachable" : "Some required services are unavailable"}
      </h2>
      {readiness === null ? (
        <p className="mt-1">
          The web interface could not contact the Tracehollow API. Check that the{" "}
          <code className="font-mono">api</code> service is running, for example with{" "}
          <code className="font-mono">docker compose ps</code>.
        </p>
      ) : (
        <ul className="mt-2 space-y-1">
          {Object.entries(readiness.checks)
            .filter(([, status]) => status !== "ok")
            .map(([name, status]) => (
              <li key={name}>
                <span className="font-medium">{CHECK_LABELS[name] ?? name}:</span>{" "}
                {CHECK_STATUS_LABELS[status] ?? status}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
