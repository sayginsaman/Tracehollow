import type { CheckStatus } from "./api-types";
import { ApiError } from "./client-api";

const MESSAGES: Record<string, string> = {
  invalid_credentials: "Incorrect username or password.",
  setup_token_invalid:
    "The setup token is not valid. Copy it from secrets/bootstrap_token on the machine running Tracehollow.",
  setup_already_completed: "Setup has already been completed. Sign in instead.",
  web_setup_disabled:
    "Web setup is disabled because no setup token is configured. Create the administrator with the command-line tool instead.",
  username_taken: "That username is already taken.",
  origin_not_allowed:
    "The request was blocked because it did not come from a trusted address. Open Tracehollow at its configured URL.",
  csrf_token_invalid: "Your security token is missing or out of date. Reload the page and try again.",
  authentication_required: "Your session has ended. Sign in again.",
  request_body_too_large: "The request was too large.",
  api_unreachable:
    "The Tracehollow API could not be reached. Check that the api service is running.",
  network_error: "The request could not be sent. Check your connection to Tracehollow.",
  database_unavailable: "The database is unavailable.",
};

export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "An unexpected error occurred.";
  if (error.code === "too_many_failed_attempts") {
    const wait = error.retryAfterSeconds;
    return wait
      ? `Too many failed sign-in attempts. Try again in ${wait} second${wait === 1 ? "" : "s"}.`
      : "Too many failed sign-in attempts. Try again later.";
  }
  const known = MESSAGES[error.code];
  if (known) return known;
  if (error.status === 422 && !error.code.startsWith("http_")) {
    return error.code.charAt(0).toUpperCase() + error.code.slice(1) + ".";
  }
  if (error.status >= 500) return `The server reported an error (HTTP ${error.status}).`;
  return `The request failed (HTTP ${error.status || "no response"}).`;
}

export const CHECK_LABELS: Record<string, string> = {
  database: "PostgreSQL database",
  migrations: "Database migrations",
  redis: "Redis broker",
  storage: "Evidence storage volume",
};

export const CHECK_STATUS_LABELS: Record<CheckStatus, string> = {
  ok: "OK",
  unavailable: "Unavailable",
  migrations_pending: "Migrations pending",
  not_writable: "Not writable",
};

const utcFormatter = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "medium",
  timeZone: "UTC",
});

/** Deterministic on server and client, so it is safe during hydration. */
export function formatUtc(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : `${utcFormatter.format(date)} UTC`;
}
