import type { CollectionMode, ConnectorDescriptor, Observation, ParameterSpec } from "./workspace-types";

export const COLLECTION_MODE_TEXT: Record<CollectionMode, { label: string; explanation: string }> = {
  synthetic_fixture: {
    label: "Synthetic fixture",
    explanation: "Generated test data. Nothing is contacted.",
  },
  direct_request: {
    label: "Direct request",
    explanation: "Tracehollow requests the address you enter. That server sees the request and your IP address.",
  },
  third_party_api: {
    label: "Third-party lookup",
    explanation: "A third-party service is asked about the input. The target itself is not contacted.",
  },
  platform_probe: {
    label: "Platform probe",
    explanation: "Each selected platform receives a request for the profile address. The platforms see the requests.",
  },
};

export const VERIFICATION_TEXT: Record<ConnectorDescriptor["verification_status"], string> = {
  synthetic: "Synthetic: no live source",
  fixture_tested: "Fixture-tested: not live-verified",
  live_verified: "Live-verified",
};

export function collectionMode(mode: string): { label: string; explanation: string } {
  return COLLECTION_MODE_TEXT[mode as CollectionMode] ?? { label: mode, explanation: "" };
}

export function defaultParameters(descriptor: ConnectorDescriptor): Record<string, unknown> {
  return Object.fromEntries(descriptor.parameters.map((spec) => [spec.name, spec.default]));
}

/** Only parameters that differ from their defaults are sent, so saved queries stay short. */
export function changedParameters(specs: ParameterSpec[], values: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const spec of specs) {
    const value = values[spec.name];
    if (value === undefined) continue;
    if (JSON.stringify(value) !== JSON.stringify(spec.default)) result[spec.name] = value;
  }
  return result;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** A short, human label for an observation from any connector. */
export function observationLabel(observation: Observation): { primary: string; secondary: string } {
  const p = observation.payload;
  switch (observation.observation_type) {
    case "candidate_account":
      return { primary: text(p.username) ?? observation.source_object_id ?? "account", secondary: `candidate on ${text(p.platform) ?? "platform"}` };
    case "web_page":
      return { primary: text(p.title) ?? text(p.final_url) ?? "web page", secondary: `HTTP ${String(p.http_status ?? "?")}` };
    case "feed":
      return { primary: text(p.title) ?? "feed", secondary: String(p.format ?? "") };
    case "feed_entry":
      return { primary: text(p.title) ?? observation.source_object_id ?? "entry", secondary: text(p.published_original) ?? "feed entry" };
    case "github_account":
      return { primary: text(p.login) ?? "account", secondary: `GitHub ${String(p.type ?? "account").toLowerCase()}` };
    case "github_repository":
      return { primary: text(p.full_name) ?? "repository", secondary: "repository" };
    case "subdomain":
      return { primary: text(p.host) ?? observation.source_object_id ?? "subdomain", secondary: Array.isArray(p.sources) ? p.sources.join(", ") : "subdomain" };
    default:
      return { primary: observation.source_object_id ?? observation.observation_type, secondary: observation.observation_type };
  }
}

export function formatQuota(quota: Record<string, unknown> | null): string {
  if (!quota) return "Not reported by this source";
  if (typeof quota.remaining === "number" && typeof quota.limit === "number") {
    const reset = typeof quota.reset_at === "string" ? `, resets ${quota.reset_at.replace("T", " ").replace("+00:00", " UTC")}` : "";
    const auth = quota.authenticated === true ? "with token" : quota.authenticated === false ? "without token" : "";
    return `${quota.remaining} of ${quota.limit} requests left${auth ? ` (${auth})` : ""}${reset}. Cost: ${String(quota.cost ?? "unknown")}.`;
  }
  return JSON.stringify(quota);
}

export function describeProgress(coverage: Record<string, unknown>): string | null {
  const progress = coverage.progress;
  if (!progress || typeof progress !== "object") return null;
  const values = progress as Record<string, unknown>;
  if (values.waiting_for_slot) return `Waiting for a free slot (at most ${String(values.limit ?? "?")} concurrent runs of this source).`;
  if (typeof values.sites_checked === "number") return `${values.sites_checked} of ${String(values.sites_selected ?? "?")} platform(s) checked.`;
  if (typeof values.subdomains_found === "number") return `${values.subdomains_found} subdomain(s) found so far.`;
  return null;
}
