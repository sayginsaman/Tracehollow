export type CheckStatus = "ok" | "unavailable" | "migrations_pending" | "not_writable";

export interface Readiness {
  status: "ready" | "not_ready";
  checks: Record<string, CheckStatus>;
}

export interface SetupStatus {
  setup_required: boolean;
}

export interface UserPublic {
  id: string;
  username: string;
  is_admin: boolean;
}

export interface SessionInfo {
  user: UserPublic;
  csrf_token: string;
  expires_at: string;
  idle_expires_at: string;
}

export interface DependencyCheck {
  status: CheckStatus;
  detail: string | null;
}

export interface SystemStatus {
  api_version: string;
  environment: string;
  ready: boolean;
  checks: Record<string, DependencyCheck>;
  checked_at: string;
}

export interface WorkerStatus {
  status: "online" | "offline" | "broker_unavailable";
  workers: { name: string }[];
  checked_at: string;
  note: string;
}

export type WorkerCheckState = "queued" | "completed" | "dispatch_failed";

export interface WorkerCheck {
  id: string;
  status: WorkerCheckState;
  requested_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
  worker_hostname: string | null;
  error_code: string | null;
}
