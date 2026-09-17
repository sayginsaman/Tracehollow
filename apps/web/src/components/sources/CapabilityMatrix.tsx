"use client";

import { VERIFICATION_TEXT, collectionMode } from "@/lib/connectors";
import type { Capability } from "@/lib/workspace-types";

export const ACCESS_METHOD_TEXT: Record<Capability["access_method"], string> = {
  official_api: "Official API",
  public_web_unofficial: "Unofficial public web access",
  unofficial_client: "Unofficial client",
  third_party_provider: "Third-party provider",
};

/** One-line availability text; blocked capabilities always say why. */
export function capabilityAvailability(capability: Capability): { tone: "ok" | "warn" | "neutral"; text: string } {
  if (capability.status === "excluded") return { tone: "neutral", text: `Excluded. ${capability.reason ?? ""}`.trim() };
  if (capability.status === "not_implemented") return { tone: "neutral", text: `Not implemented. ${capability.reason ?? ""}`.trim() };
  if (capability.available) return { tone: "ok", text: "Available" };
  return { tone: "warn", text: capability.blocked_reason ?? "Unavailable" };
}

function List({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div>
      <dt className="font-medium text-muted">{label}</dt>
      <dd>{values.join("; ")}</dd>
    </div>
  );
}

export function CapabilityMatrix({ capabilities }: { capabilities: Capability[] }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">Capabilities</h3>
      <p className="mb-2 text-xs text-muted">
        Every access method considered for this platform. Only implemented capabilities can be selected in saved queries;
        missing credentials block collection and live verification rather than producing results.
      </p>
      <ul className="space-y-3">
        {capabilities.map((capability) => {
          const availability = capabilityAvailability(capability);
          const toneClass = { ok: "border-ok/40 text-ok", warn: "border-warn/40 text-warn", neutral: "border-line text-muted" }[availability.tone];
          return (
            <li key={capability.name} className="rounded-md border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{capability.label}</span>
                <span className="rounded border border-line px-1.5 py-0.5 text-xs">{ACCESS_METHOD_TEXT[capability.access_method]}</span>
                {capability.verification_status ? (
                  <span className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">{VERIFICATION_TEXT[capability.verification_status]}</span>
                ) : null}
              </div>
              <p className={`mt-1 rounded border px-2 py-1 text-xs ${toneClass}`}>{availability.text}</p>
              {capability.status === "implemented" ? (
                <dl className="mt-2 grid gap-1 text-xs">
                  <div>
                    <dt className="font-medium text-muted">Provider</dt>
                    <dd>
                      {capability.provider}
                      {capability.collection_mode ? ` · ${collectionMode(capability.collection_mode).label}` : ""}
                    </dd>
                  </div>
                  <List label="Account types" values={capability.account_types} />
                  <List label="Content" values={capability.content_types} />
                  <List label="Returned fields" values={capability.returned_fields} />
                  <List label="Never returned" values={capability.unavailable_fields} />
                  <List label="Stable identifiers" values={capability.stable_identifiers} />
                  <List label="Pagination and coverage" values={[capability.pagination]} />
                  <List label="Session and credentials" values={[capability.session_requirements]} />
                  <List label="Restrictions" values={[capability.restrictions]} />
                  <List label="Cost and quota" values={[capability.cost_quota]} />
                  <List label="Checked against" values={capability.references} />
                </dl>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
