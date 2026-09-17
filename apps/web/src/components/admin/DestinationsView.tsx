"use client";

import { Eye, Plus, Webhook, X } from "lucide-react";
import { useState } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Delivery, Destination, Page, PayloadPreview } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  ChoiceField,
  ConfirmAction,
  CopyButton,
  DataTable,
  Disclosure,
  EmptyState,
  ErrorNotice,
  Field,
  FieldGroup,
  IconButton,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  PageHeader,
  Panel,
  StatusBadge,
  Td,
  TextInput,
  Th,
  Timestamp,
  Tr,
  humanize,
  type Tone,
} from "../ui";
import { AdminGate } from "./AdminGate";

const EVENT_TYPES: [string, string][] = [
  ["change_detected", "Meaningful changes"],
  ["action_required", "Failures that need action"],
  ["budget_exhausted", "Budget used up"],
  ["run_completed", "Completed runs"],
];
const DELIVERY_TONES: Record<Delivery["status"], Tone> = { pending: "neutral", delivered: "ok", failed: "bad", blocked: "warn" };

function PayloadView({ preview }: { preview: PayloadPreview }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-ink">
        <Mono>{preview.method}</Mono> <Mono className="break-all">{preview.url}</Mono>
      </p>
      <pre className="max-h-80 overflow-auto rounded-md bg-sunken p-3 font-mono text-code text-ink">{JSON.stringify({ headers: preview.headers, body: preview.body }, null, 2)}</pre>
      <ul className="max-w-[72ch] list-inside list-disc space-y-0.5 text-xs text-muted">
        {preview.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

function SecretOnce({ secret, onDismiss }: { secret: string; onDismiss: () => void }) {
  return (
    <Notice
      tone="warn"
      title="Copy the signing secret now"
      actions={
        <Button size="sm" onClick={onDismiss}>
          I stored it
        </Button>
      }
    >
      <p>It is shown once and stored encrypted. The receiver uses it to verify the x-tracehollow-signature header.</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Mono className="rounded bg-surface px-2 py-1 break-all text-ink">{secret}</Mono>
        <CopyButton value={secret} label="Copy secret" />
      </div>
    </Notice>
  );
}

function CreateDestination({ onCreated, onClose }: { onCreated: (created: Destination) => Promise<void>; onClose: () => void }) {
  const { mutate } = useSession();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("https://");
  const [types, setTypes] = useState<string[]>(["change_detected", "action_required", "budget_exhausted"]);
  const [rate, setRate] = useState("30");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="New webhook destination" description="Created disabled. Nothing is sent until you preview the payload and enable it." actions={<IconButton icon={X} label="Close form" onClick={onClose} />}>
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const created = await mutate<Destination>("/api/v1/admin/notification-destinations", {
              body: { name, url, event_types: types, max_per_minute: Number(rate) },
            });
            await onCreated(created);
          } catch (caught) {
            setError(describeError(caught));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Name" htmlFor="destination-name">
            <TextInput id="destination-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} />
          </Field>
          <Field label="Receiver URL" htmlFor="destination-url" hint="HTTPS on an allowed port. No query string or tokens in the URL; private addresses are refused unless allowed for collection.">
            <TextInput id="destination-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} required maxLength={2048} />
          </Field>
        </div>
        <FieldGroup legend="Event types this destination may receive">
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {EVENT_TYPES.map(([key, label]) => (
              <ChoiceField
                key={key}
                label={label}
                checked={types.includes(key)}
                onChange={(event) => setTypes(event.target.checked ? [...types, key] : types.filter((item) => item !== key))}
              />
            ))}
          </div>
        </FieldGroup>
        <Field label="Deliveries per minute at most" htmlFor="destination-rate" className="max-w-48">
          <TextInput id="destination-rate" type="number" min={1} max={600} value={rate} onChange={(event) => setRate(event.target.value)} />
        </Field>
        <ActionError message={error} />
        <Button type="submit" variant="primary" busy={busy} disabled={busy || !name || types.length === 0}>
          Create disabled destination
        </Button>
      </form>
    </Panel>
  );
}

function Deliveries({ destinationId }: { destinationId: string }) {
  const deliveries = useResource<Page<Delivery>>(`/api/v1/admin/notification-destinations/${destinationId}/deliveries?limit=25`);
  if (deliveries.state === "error") return <ErrorNotice error={deliveries.error} onRetry={() => void deliveries.reload()} />;
  if (!deliveries.data) return <LoadingState label="Loading deliveries…" />;
  if (deliveries.data.items.length === 0) return <p className="text-sm text-muted">No deliveries yet.</p>;
  return (
    <DataTable caption="Recent deliveries" minWidth="44rem">
      <thead>
        <tr>
          <Th>Created</Th>
          <Th>Event</Th>
          <Th>Status</Th>
          <Th>Attempts</Th>
          <Th>Last result</Th>
        </tr>
      </thead>
      <tbody>
        {deliveries.data.items.map((delivery) => (
          <Tr key={delivery.id}>
            <Td className="text-sm whitespace-nowrap">
              <Timestamp value={delivery.created_at} />
            </Td>
            <Td className="text-sm">
              {humanize(delivery.event_type)}
              <Mono className="block text-xs text-muted">{delivery.event_id}</Mono>
            </Td>
            <Td>
              <StatusBadge tone={DELIVERY_TONES[delivery.status]} label={humanize(delivery.status)} />
            </Td>
            <Td className="text-sm">{delivery.attempts}</Td>
            <Td className="text-sm">
              {delivery.last_response_status ? `HTTP ${delivery.last_response_status}` : null}
              {delivery.last_error_code ? <span className="block text-xs text-muted">{humanize(delivery.last_error_code)}</span> : null}
              {delivery.status === "pending" ? (
                <span className="block text-xs text-muted">
                  Next attempt <Timestamp value={delivery.next_attempt_at} />
                </span>
              ) : null}
            </Td>
          </Tr>
        ))}
      </tbody>
    </DataTable>
  );
}

function DestinationItem({ destination, adapterEnabled, onChanged }: { destination: Destination; adapterEnabled: boolean; onChanged: (secret?: string | null) => Promise<void> }) {
  const { mutate } = useSession();
  const [preview, setPreview] = useState<PayloadPreview | null>(null);
  const [confirmHost, setConfirmHost] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const path = `/api/v1/admin/notification-destinations/${destination.id}`;

  async function act(key: string, action: () => Promise<Destination | void>) {
    setBusy(key);
    setError(null);
    try {
      const result = await action();
      await onChanged(result && "signing_secret" in result ? result.signing_secret : null);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="space-y-4 px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">{destination.name}</span>
            {destination.enabled ? <StatusBadge tone="ok" label="Enabled" /> : <StatusBadge tone="neutral" label="Disabled" />}
          </p>
          <Mono className="mt-0.5 block text-sm break-all text-muted">{destination.url}</Mono>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            icon={Eye}
            busy={busy === "preview"}
            onClick={() =>
              void act("preview", async () => {
                setPreview(await mutate<PayloadPreview>(`${path}/preview`));
              })
            }
          >
            Preview payload
          </Button>
          {destination.enabled ? (
            <Button size="sm" busy={busy === "disable"} onClick={() => void act("disable", () => mutate(`${path}/disable`))}>
              Disable
            </Button>
          ) : null}
        </div>
      </div>
      <KeyValue
        compact
        items={[
          ["Events", destination.event_types.map((type) => EVENT_TYPES.find(([key]) => key === type)?.[1] ?? type).join(", ")],
          ["Rate limit", `${destination.max_per_minute} per minute`],
          ["Signed", destination.signing ? "HMAC-SHA256 with a stored secret" : "No"],
          ["Monitor subscriptions", String(destination.subscriptions)],
          ["Last delivery", destination.last_delivery_at ? <span key="last">{humanize(destination.last_status ?? "")} <Timestamp value={destination.last_delivery_at} /></span> : "Never"],
          ...(destination.consecutive_failures ? ([["Consecutive failures", String(destination.consecutive_failures)]] as [string, React.ReactNode][]) : []),
        ]}
      />
      <ActionError message={error} />
      {preview ? <PayloadView preview={preview} /> : null}
      {!destination.enabled && adapterEnabled ? (
        <form
          className="space-y-2 rounded-md border border-line bg-sunken p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act("enable", () => mutate(`${path}/enable`, { body: { confirm_host: confirmHost } }));
          }}
        >
          <p className="max-w-[72ch] text-sm text-ink">
            Enabling sends real HTTP requests to <Mono>{destination.host}</Mono> whenever a subscribed monitor produces one of the selected events. Review the payload
            preview first; payloads carry identifiers, counts and reason codes only.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label={`Type ${destination.host} to confirm`} htmlFor={`confirm-${destination.id}`}>
              <TextInput id={`confirm-${destination.id}`} value={confirmHost} onChange={(event) => setConfirmHost(event.target.value)} autoComplete="off" className="w-72" />
            </Field>
            <Button type="submit" variant="primary" busy={busy === "enable"} disabled={busy !== null || !preview || confirmHost.trim().toLowerCase() !== destination.host.toLowerCase()}>
              Enable deliveries
            </Button>
          </div>
          {!preview ? <p className="text-xs text-muted">Preview the payload before enabling.</p> : null}
        </form>
      ) : null}
      <Disclosure summary="Recent deliveries">
        <Deliveries destinationId={destination.id} />
      </Disclosure>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" busy={busy === "rotate"} onClick={() => void act("rotate", () => mutate<Destination>(path, { method: "PATCH", body: { rotate_secret: true } }))}>
          Rotate signing secret
        </Button>
        <ConfirmAction
          label="Delete"
          confirmLabel="Delete destination"
          message="Delete this destination and its subscriptions? Pending deliveries are dropped."
          busy={busy === "delete"}
          onConfirm={() => void act("delete", () => mutate(path, { method: "DELETE" }))}
        />
      </div>
    </li>
  );
}

function Destinations() {
  const status = useResource<{ adapter_enabled: boolean; setting: string }>("/api/v1/admin/notification-destinations/status");
  const destinations = useResource<Destination[]>("/api/v1/admin/notification-destinations");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const adapterEnabled = status.data?.adapter_enabled ?? false;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notification destinations"
        description="Optional webhooks that receive minimal, signed event summaries from subscribed monitors. In-app notifications work without any destination."
        actions={
          adapterEnabled && !creating ? (
            <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>
              New destination
            </Button>
          ) : undefined
        }
      />
      {status.state === "error" ? <ErrorNotice error={status.error} onRetry={() => void status.reload()} /> : null}
      {status.data && !adapterEnabled ? (
        <Notice tone="neutral" title="External notifications are off">
          The webhook adapter is disabled on this installation. An operator can turn it on with <Mono>{status.data.setting}=true</Mono> in the environment and
          restart the API and workers. Existing destinations do not send while it is off.
        </Notice>
      ) : null}
      {secret ? <SecretOnce secret={secret} onDismiss={() => setSecret(null)} /> : null}
      {creating ? (
        <CreateDestination
          onClose={() => setCreating(false)}
          onCreated={async (created) => {
            setCreating(false);
            setSecret(created.signing_secret ?? null);
            await destinations.reload();
          }}
        />
      ) : null}
      <Panel title="Destinations" flush>
        {destinations.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={destinations.error} onRetry={() => void destinations.reload()} />
          </div>
        ) : null}
        {!destinations.data && destinations.state === "loading" ? <LoadingState className="p-4" label="Loading destinations…" /> : null}
        {destinations.data && destinations.data.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={Webhook} title="No destinations">
              Add a destination only for a receiver you control and are authorized to send investigation metadata to.
            </EmptyState>
          </div>
        ) : null}
        <ul className="divide-y divide-line">
          {destinations.data?.map((destination) => (
            <DestinationItem
              key={destination.id}
              destination={destination}
              adapterEnabled={adapterEnabled}
              onChanged={async (newSecret) => {
                if (newSecret) setSecret(newSecret);
                await destinations.reload();
              }}
            />
          ))}
        </ul>
      </Panel>
      <Panel title="What leaves Tracehollow">
        <ul className="max-w-[72ch] list-inside list-disc space-y-1 text-sm text-ink">
          <li>Event identifier, type, severity, time, case, monitor and run identifiers, change counts and reason codes.</li>
          <li>Never: case or monitor names, query inputs, collected values, evidence, AI questions or answers, credentials.</li>
          <li>Every delivery is rechecked right before sending: destination enabled, subscription, case state and the subscriber&apos;s access.</li>
          <li>A delivery can arrive more than once. Receivers deduplicate on the event identifier.</li>
          <li>Deleting or disabling here does not recall anything already delivered.</li>
        </ul>
      </Panel>
    </div>
  );
}

export function DestinationsView() {
  return (
    <AdminGate permission="notification_destinations.manage">
      <Destinations />
    </AdminGate>
  );
}
