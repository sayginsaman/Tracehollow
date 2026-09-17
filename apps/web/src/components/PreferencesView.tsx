"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/lib/permissions";
import { useSession } from "@/lib/session-context";
import { applyThemePreference, readThemePreference, type ThemePreference } from "@/lib/theme";

import { ActionError, Button, Field, KeyValue, Notice, PageHeader, Panel, TextInput, Timestamp, cn } from "./ui";

const THEMES: { value: ThemePreference; label: string; description: string; icon: typeof Sun }[] = [
  { value: "system", label: "Match the operating system", description: "Light or dark follows your device setting.", icon: Monitor },
  { value: "light", label: "Light", description: "Dark text on warm paper. Recommended for long reading in daylight.", icon: Sun },
  { value: "dark", label: "Dark", description: "Light text on warm charcoal, for dim rooms.", icon: Moon },
];

function PasswordChange() {
  const { mutate } = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDone(false);
    if (next !== repeat) return setError("The new passwords do not match.");
    setBusy(true);
    setError(null);
    try {
      await mutate("/api/v1/auth/password", { body: { current_password: current, new_password: next } });
      setCurrent("");
      setNext("");
      setRepeat("");
      setDone(true);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-3" aria-label="Change password">
      <Field label="Current password" htmlFor="password-current">
        <TextInput id="password-current" type="password" value={current} onChange={(event) => setCurrent(event.target.value)} autoComplete="current-password" required />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="New password" htmlFor="password-new" hint="At least 12 characters.">
          <TextInput id="password-new" type="password" value={next} onChange={(event) => setNext(event.target.value)} autoComplete="new-password" required />
        </Field>
        <Field label="Repeat new password" htmlFor="password-repeat">
          <TextInput id="password-repeat" type="password" value={repeat} onChange={(event) => setRepeat(event.target.value)} autoComplete="new-password" required />
        </Field>
      </div>
      <ActionError message={error} />
      {done ? <Notice tone="ok" live>Password changed. Your other sessions were signed out; this one stays signed in.</Notice> : null}
      <Button type="submit" busy={busy} disabled={busy || !current || !next || !repeat}>
        Change password
      </Button>
    </form>
  );
}

export function PreferencesView() {
  const { session } = useSession();
  const [theme, setTheme] = useState<ThemePreference>("system");

  useEffect(() => {
    // The stored preference is only readable in the browser.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(readThemePreference());
  }, []);

  function choose(value: ThemePreference) {
    setTheme(value);
    applyThemePreference(value);
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Preferences" description="Settings for this browser and your account. Source credentials are managed on Sources; case settings live inside each case." />

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Panel title="Appearance" description="Stored in this browser only.">
          <fieldset>
            <legend className="sr-only">Theme</legend>
            <div className="space-y-2">
              {THEMES.map((option) => {
                const Icon = option.icon;
                const checked = theme === option.value;
                return (
                  <label
                    key={option.value}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors",
                      checked ? "border-accent bg-accent-soft" : "border-line hover:bg-sunken",
                    )}
                  >
                    <input type="radio" name="theme" value={option.value} checked={checked} onChange={() => choose(option.value)} className="mt-1 shrink-0" />
                    <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                    <span className="min-w-0 text-sm">
                      <span className="block font-medium text-ink">{option.label}</span>
                      <span className="block text-muted">{option.description}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        </Panel>

        <Panel title="Account and session">
          <div className="space-y-4 text-sm">
            <KeyValue
              items={[
                ["Username", session.user.username],
                ["Account role", `${ROLE_LABELS[session.user.role] ?? session.user.role}. ${ROLE_DESCRIPTIONS[session.user.role] ?? ""}`],
                ["Session expires", <Timestamp key="expires" value={session.expires_at} />],
                ["Idle timeout", <Timestamp key="idle" value={session.idle_expires_at} />],
              ]}
            />
            <p className="text-muted">
              Your role in each case is shown inside the case. Signing out ends this session on the server. If you forget your password, an administrator can set a
              new one.
            </p>
          </div>
        </Panel>

        <Panel title="Password">
          <PasswordChange />
        </Panel>

        <Panel title="Related settings" className="lg:col-span-2">
          <ul className="grid gap-3 text-sm sm:grid-cols-2">
            <li>
              <Link href="/sources" className="font-medium text-accent hover:underline">
                Sources
              </Link>
              <p className="text-muted">Connector capabilities, access limits, verification status and write-only credentials.</p>
            </li>
            <li>
              <Link href="/status" className="font-medium text-accent hover:underline">
                Environment status
              </Link>
              <p className="text-muted">Database, broker, storage and worker checks.</p>
            </li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}
