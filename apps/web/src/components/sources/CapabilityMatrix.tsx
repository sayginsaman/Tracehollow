"use client";

import { CircleCheck, CircleSlash, LockKeyhole, Wrench } from "lucide-react";

import { VERIFICATION_TEXT, collectionMode } from "@/lib/connectors";
import type { Capability } from "@/lib/workspace-types";

import { Disclosure, StatusBadge, SubHeading, Tag } from "../ui";

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
    <div className="contents">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{values.join("; ")}</dd>
    </div>
  );
}

export function CapabilityMatrix({ capabilities }: { capabilities: Capability[] }) {
  return (
    <div className="space-y-2">
      <SubHeading>Capabilities</SubHeading>
      <p className="max-w-[72ch] text-xs text-muted">
        Every access method considered for this platform. Only implemented capabilities can be selected in saved queries; missing credentials
        block collection and live verification rather than producing results.
      </p>
      <ul className="divide-y divide-line rounded-md border border-line">
        {capabilities.map((capability) => {
          const availability = capabilityAvailability(capability);
          const Icon =
            capability.status === "excluded" ? CircleSlash : capability.status === "not_implemented" ? Wrench : capability.available ? CircleCheck : LockKeyhole;
          const toneClass = { ok: "text-ok", warn: "text-warn", neutral: "text-muted" }[availability.tone];
          return (
            <li key={capability.name} className="space-y-2 px-3 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-ink">{capability.label}</span>
                <Tag>{ACCESS_METHOD_TEXT[capability.access_method]}</Tag>
                {capability.verification_status ? (
                  capability.verification_status === "live_verified" ? (
                    <StatusBadge tone="ok" label={VERIFICATION_TEXT.live_verified} />
                  ) : (
                    <Tag>{VERIFICATION_TEXT[capability.verification_status]}</Tag>
                  )
                ) : null}
              </div>
              <p className={`flex items-start gap-1.5 text-sm ${toneClass}`}>
                <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                <span className="min-w-0 [overflow-wrap:anywhere]">{availability.text}</span>
              </p>
              {capability.status === "implemented" ? (
                <Disclosure summary="What it returns, needs and costs">
                  <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-[minmax(8rem,max-content)_minmax(0,1fr)]">
                    <List label="Provider" values={[`${capability.provider}${capability.collection_mode ? ` · ${collectionMode(capability.collection_mode).label}` : ""}`]} />
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
                </Disclosure>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
