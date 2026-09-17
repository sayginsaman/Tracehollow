"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/lib/session-context";
import { applyThemePreference, readThemePreference, type ThemePreference } from "@/lib/theme";

import { KeyValue, PageHeader, Panel, Timestamp, cn } from "./ui";

const THEMES: { value: ThemePreference; label: string; description: string; icon: typeof Sun }[] = [
  { value: "system", label: "Match the operating system", description: "Light or dark follows your device setting.", icon: Monitor },
  { value: "light", label: "Light", description: "Dark text on warm paper. Recommended for long reading in daylight.", icon: Sun },
  { value: "dark", label: "Dark", description: "Light text on warm charcoal, for dim rooms.", icon: Moon },
];

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
                ["Role", session.user.is_admin ? "Administrator: can manage source credentials" : "Analyst"],
                ["Session expires", <Timestamp key="expires" value={session.expires_at} />],
                ["Idle timeout", <Timestamp key="idle" value={session.idle_expires_at} />],
              ]}
            />
            <p className="text-muted">
              Passwords are changed with the command-line tool on the machine that runs Tracehollow. Signing out ends this session on the server.
            </p>
          </div>
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
