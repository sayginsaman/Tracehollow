export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type CaseStatus = "active" | "archived" | "deleting" | "deletion_failed";

export interface CaseCounts {
  entities: number;
  relationships: number;
  evidence: number;
  notes: number;
  saved_queries: number;
  query_runs: number;
  active_runs: number;
}

export interface CaseSummary {
  id: string;
  title: string;
  purpose: string;
  scope: string;
  tags: string[];
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  /** Effective role of the signed-in account in this case. */
  my_role?: CaseRole | null;
}

export type CaseRole = "analyst" | "viewer";

export interface CaseDetail extends CaseSummary {
  counts: CaseCounts;
  /** Case permissions of my_role, for example "case.edit" or "exports.create". */
  permissions?: string[];
}

export interface CaseDeletion {
  id: string;
  case_id: string;
  status: "queued" | "running" | "completed" | "failed";
  attempts: number;
  progress_note: string | null;
  error_code: string | null;
  removed_counts: Record<string, number>;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Note {
  id: string;
  case_id: string;
  entity_id: string | null;
  relationship_id: string | null;
  evidence_id: string | null;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface Identifier {
  id: string;
  identifier_type: string;
  platform: string | null;
  original_value: string;
  normalized_value: string;
  created_at: string;
}

export interface Entity {
  id: string;
  case_id: string;
  entity_type: string;
  display_name: string;
  description: string;
  attributes: Record<string, unknown>;
  origin: string;
  created_by_query_run_id: string | null;
  created_at: string;
  updated_at: string;
  identifiers: Identifier[];
}

export interface EntityRef {
  id: string;
  display_name: string;
  entity_type: string;
}

export interface Relationship {
  id: string;
  case_id: string;
  source: EntityRef;
  target: EntityRef;
  predicate: string;
  origin: string;
  review_status: string;
  description: string;
  valid_from: string | null;
  valid_to: string | null;
  created_by_query_run_id: string | null;
  reference_count: number;
  created_at: string;
  updated_at: string;
}

export interface RelationshipReference {
  id: string;
  stance: "supports" | "contradicts";
  note: string | null;
  evidence_id: string | null;
  evidence_title: string | null;
  evidence_acquisition_method: string | null;
  evidence_sha256: string | null;
  observation_id: string | null;
  observation_type: string | null;
  observation_collected_at: string | null;
  query_run_id: string | null;
  created_at: string;
}

export interface AnalystDecision {
  id: string;
  decision_type: string;
  previous_value: string;
  new_value: string;
  rationale: string | null;
  decided_at: string;
}

export interface RelationshipDetail extends Relationship {
  references: RelationshipReference[];
  decisions: AnalystDecision[];
}

export interface EntityDetail {
  entity: Entity;
  linked_evidence: {
    link_id: string;
    evidence_id: string;
    title: string;
    acquisition_method: string;
    sha256: string;
    note: string | null;
    created_at: string;
  }[];
  relationships: Relationship[];
  shared_identifiers: {
    identifier_type: string;
    normalized_value: string;
    entity_id: string;
    display_name: string;
    entity_type: string;
  }[];
  observation_count: number;
}

export interface Vocabulary {
  entity_types: string[];
  identifier_types: string[];
  suggested_predicates: string[];
  origins: string[];
  review_statuses: string[];
}

export interface Observation {
  id: string;
  entity_id: string | null;
  evidence_id: string | null;
  query_run_id: string | null;
  connector_run_id: string | null;
  observation_type: string;
  source_object_id: string | null;
  payload: Record<string, unknown>;
  collected_at: string;
  event_time: string | null;
  source_published_at: string | null;
  created_at: string;
}

export interface Evidence {
  id: string;
  case_id: string;
  kind: "text" | "json" | "html" | "xml" | "pdf" | "archive" | "binary";
  title: string;
  original_filename: string | null;
  content_type: string;
  size_bytes: number;
  sha256: string;
  acquisition_method: "authorized_import" | "synthetic_fixture" | "connector_collection";
  import_origin: string | null;
  source_reference: string | null;
  source_published_at: string | null;
  source_published_at_original: string | null;
  collected_at: string;
  created_at: string;
  connector_id: string | null;
  connector_version: string | null;
  query_run_id: string | null;
  connector_run_id: string | null;
  page_index: number | null;
  description: string;
  synthetic: boolean;
  collection_mode: CollectionMode | null;
  access_category: "public" | "credentialed" | null;
  derived_from_evidence_id: string | null;
  collection_metadata: Record<string, unknown>;
}

export interface ImportResult {
  evidence: Evidence;
  filename_sanitized: boolean;
  duplicate_of: string[];
}

export interface EvidenceDetail {
  evidence: Evidence;
  integrity: { status: string; checked_at: string };
  linked_entities: { link_id: string; entity_id: string; display_name: string; entity_type: string; note: string | null }[];
  linked_relationships: {
    reference_id: string;
    relationship_id: string;
    predicate: string;
    stance: string;
    source_entity_id: string;
    target_entity_id: string;
  }[];
  observation_count: number;
  derived_evidence: string[];
  duplicate_of: string[];
  index: {
    status: string;
    chunk_count: number;
    attempts: number;
    error_code: string | null;
    error_detail: string | null;
    indexed_at: string | null;
  } | null;
}

export interface EvidencePreview {
  evidence_id: string;
  kind: string;
  encoding: string;
  text: string;
  pretty_json: string | null;
  truncated: boolean;
  preview_bytes: number;
  size_bytes: number;
  previewable?: boolean;
  note?: string | null;
}

export type CollectionMode = "synthetic_fixture" | "direct_request" | "third_party_api" | "platform_probe";

export interface ParameterSpec {
  name: string;
  kind: "choice" | "multi_choice" | "integer" | "boolean";
  label: string;
  description: string;
  default: unknown;
  choices: Record<string, string> | null;
  minimum: number | null;
  maximum: number | null;
}

export interface CredentialStatus {
  name: string;
  label: string;
  description: string;
  required: boolean;
  configured: boolean;
  usable: boolean;
  updated_at: string | null;
  last_used_at: string | null;
  last_result: string | null;
}

export interface ConnectorHealth {
  last_run_at: string | null;
  last_outcome: string | null;
  last_error_code: string | null;
  last_quota: Record<string, unknown> | null;
  recent_outcomes: Record<string, number>;
}

export interface ConnectorDescriptor {
  connector_id: string;
  version: string;
  display_name: string;
  synthetic: boolean;
  description: string;
  supported_input_types: string[];
  collection_mode: CollectionMode;
  credential_requirements: string;
  coverage: string;
  max_pages: number;
  max_items_per_page: number;
  timeout_seconds: number;
  retry_max_attempts: number;
  retryable_outcomes: string[];
  output_schema: string;
  cost_model: string | null;
  quota_notes: string | null;
  cache_policy: string;
  max_concurrent_runs: number;
  min_request_interval_seconds: number;
  provider_terms: string | null;
  documentation: string | null;
  last_live_verification: string | null;
  verification_status: "synthetic" | "fixture_tested" | "live_verified";
  parameters: ParameterSpec[];
  credentials: CredentialStatus[];
  health: ConnectorHealth;
  capabilities?: Capability[];
}

export interface Capability {
  name: string;
  label: string;
  status: "implemented" | "not_implemented" | "excluded";
  access_method: "official_api" | "public_web_unofficial" | "unofficial_client" | "third_party_provider";
  provider: string;
  collection_mode: CollectionMode | null;
  account_types: string[];
  content_types: string[];
  returned_fields: string[];
  unavailable_fields: string[];
  stable_identifiers: string[];
  pagination: string;
  session_requirements: string;
  restrictions: string;
  cost_quota: string;
  verification_status: "synthetic" | "fixture_tested" | "live_verified" | null;
  last_live_verification: string | null;
  credential_names: string[];
  reason: string | null;
  references: string[];
  available: boolean;
  blocked_reason: string | null;
}

export interface SavedQuery {
  id: string;
  case_id: string;
  name: string;
  input_type: string;
  input_value: string;
  connector_ids: string[];
  collection_mode: string;
  parameters: Record<string, unknown>;
  limits: { max_pages?: number; max_items_per_page?: number };
  run_counter: number;
  created_at: string;
  updated_at: string;
  synthetic: boolean;
  last_run_id: string | null;
  last_run_status: string | null;
}

export type RunStatus = "queued" | "running" | "completed" | "partial" | "failed" | "canceled";

export interface ConnectorRun {
  id: string;
  position: number;
  connector_id: string;
  connector_version: string;
  status: RunStatus;
  outcome: string | null;
  pages_completed: number;
  items_collected: number;
  fetch_attempts: number;
  retries: number;
  last_error_code: string | null;
  last_error_detail: string | null;
  retry_after_seconds: number | null;
  coverage: Record<string, unknown>;
  coverage_note: string | null;
  quota_usage: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface QueryRun {
  id: string;
  case_id: string;
  saved_query_id: string | null;
  saved_query_name: string | null;
  run_number: number;
  status: RunStatus;
  parameters_snapshot: {
    saved_query_name?: string;
    input_type?: string;
    input_value?: string;
    parameters?: Record<string, unknown>;
    limits?: Record<string, unknown>;
    connectors?: { id: string; version: string; synthetic: boolean }[];
    captured_at?: string;
  } & Record<string, unknown>;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  error_code: string | null;
  synthetic: boolean;
  /** Per-connector outcomes in connector order; null while a connector has not finished. */
  connector_outcomes?: (string | null)[];
  evidence_count: number;
  observation_count: number;
  dispatch_status: string | null;
  monitor_id?: string | null;
  results_expired_at?: string | null;
}

export interface QueryRunDetail extends QueryRun {
  connector_runs: ConnectorRun[];
  entity_count: number;
  relationship_count: number;
}

export interface GraphData {
  nodes: { id: string; label: string; entity_type: string; origin: string }[];
  edges: { id: string; source: string; target: string; predicate: string; origin: string; review_status: string }[];
  focus_entity_id: string | null;
  depth: number;
  max_nodes: number;
  truncated: boolean;
  total_entities: number;
  total_relationships: number;
}

export const TERMINAL_RUN_STATUSES: RunStatus[] = ["completed", "partial", "failed", "canceled"];

// -- Evidence-grounded AI --------------------------------------------------------------------

export type AiMode = "disabled" | "local_only" | "cloud_allowed";
export type AiRunStatus = "queued" | "running" | "completed" | "failed" | "canceled";
export type ClaimKind = "fact" | "count" | "inference" | "conflict" | "insufficient";
export type AnswerStatus = "answered" | "partially_answered" | "insufficient_evidence";
export const TERMINAL_AI_RUN_STATUSES: AiRunStatus[] = ["completed", "failed", "canceled"];

export interface ProviderCheck {
  provider: string;
  location: string;
  configured: boolean;
  reachable: boolean | null;
  generation_model: string | null;
  generation_model_available: boolean | null;
  embedding_model: string | null;
  embedding_model_available: boolean | null;
  error_code: string | null;
  checked_at: string;
}

export interface AiStatus {
  enabled: boolean;
  local_provider: string;
  local_location: string;
  local_generation_model: string;
  local_embedding_model: string;
  synthetic: boolean;
  cloud_provider: string;
  cloud_model: string | null;
  cloud_configured: boolean;
  checks: ProviderCheck[];
}

export interface IndexCounts {
  pending: number;
  indexing: number;
  indexed: number;
  stale: number;
  failed: number;
  canceled: number;
  total: number;
}

export interface CaseAi {
  enabled: boolean;
  mode: AiMode;
  policy_version: number;
  local_location: string;
  cloud_available: boolean;
  index: IndexCounts;
  embedding_profile: {
    provider: string;
    model: string;
    dimensions: number;
    chunking_version: number;
    indexing_version: number;
    activated_at: string | null;
    synthetic: boolean;
  } | null;
  active_runs: number;
}

export interface IndexItem {
  evidence_id: string;
  title: string;
  acquisition_method: string;
  status: string;
  attempts: number;
  chunk_count: number;
  error_code: string | null;
  error_detail: string | null;
  queued_at: string;
  indexed_at: string | null;
  available_at: string;
}

export interface AiConversation {
  id: string;
  case_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface CitationRef {
  citation_id: string;
  label: string;
  ref_type: "chunk" | "tool";
}

export interface AnswerClaim {
  text: string;
  kind: ClaimKind;
  citations: CitationRef[];
  /** False when the statement is supported but does not answer the question that was asked. */
  answers_question?: boolean;
  /** "question_subject", "other_subject", "other_period" or "unspecified" (nothing comparable). */
  applicability?: string;
  about?: { subject: string; attribute: string; value: string; as_of: string };
  /** For a conflict claim: "disagreement", "change_over_time" or "undetermined". */
  difference_type?: string;
}

export interface SuggestionItem {
  relationship_id: string;
  source: { entity_id: string; display_name: string };
  target: { entity_id: string; display_name: string };
  predicate: string;
  rationale: string;
  citations: CitationRef[];
}

export interface AnswerPayload {
  status: AnswerStatus;
  claims?: AnswerClaim[];
  suggestions?: SuggestionItem[];
  rejected?: { reason: string }[];
  limitations?: string[];
  coverage_notes?: string[];
  server_notes?: string[];
  synthetic_model?: boolean;
}

export interface CitationSummary {
  id: string;
  label: string;
  ref_type: string;
  evidence_id: string | null;
  evidence_title: string | null;
  tool_name: string | null;
  source_available: boolean;
}

export interface AiMessage {
  id: string;
  role: "user" | "assistant";
  kind: "question" | "answer" | "summary" | "suggestions";
  content: string;
  answer: AnswerPayload | null;
  ai_run_id: string | null;
  created_at: string;
  citations: CitationSummary[];
}

export interface AiRun {
  id: string;
  case_id: string;
  conversation_id: string | null;
  run_type: "answer" | "summary" | "relationship_suggestions";
  status: AiRunStatus;
  stage: string;
  question: string | null;
  requested_location: string;
  provider: string | null;
  model: string | null;
  processing_location: string | null;
  prompt_template_version: string | null;
  usage: {
    input_tokens?: number | null;
    output_tokens?: number | null;
    source?: string;
    cost?: string;
  };
  coverage: { notes?: string[] };
  validation: { claims_removed?: { kind: string; reason: string }[]; server_notes?: string[] };
  tool_calls: { ref?: string; tool: string; arguments?: Record<string, unknown>; result?: Record<string, unknown>; rejected?: string }[];
  retrieval: { chunks?: { chunk_id: string; evidence_id: string }[]; retrievers?: Record<string, { used: boolean; reason?: string }> };
  error_code: string | null;
  error_detail: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  synthetic: boolean;
}

export interface ConversationDetail {
  conversation: AiConversation;
  messages: AiMessage[];
  runs: AiRun[];
}

export interface Passage {
  evidence_id: string | null;
  evidence_title: string | null;
  acquisition_method: string | null;
  synthetic: boolean;
  collected_at: string | null;
  source_published_at: string | null;
  source_published_at_original: string | null;
  source_reference: string | null;
  kind: string | null;
  status: string;
  integrity: string | null;
  before: string | null;
  passage: string | null;
  after: string | null;
  char_start: number | null;
  char_end: number | null;
  json_pointer: string | null;
  json_value: string | null;
  chunk_text: string | null;
  quote: string | null;
}

export interface CitationDetail {
  id: string;
  label: string;
  ref_type: string;
  claim_index: number;
  ai_run_id: string;
  tool_name: string | null;
  tool_result: { tool?: string; arguments?: Record<string, unknown>; result?: Record<string, unknown> } | null;
  passage: Passage | null;
}

export interface SearchResult {
  query: string;
  hits: {
    chunk_id: string;
    evidence_id: string;
    evidence_title: string;
    acquisition_method: string;
    synthetic: boolean;
    chunk_index: number;
    kind: string;
    snippet: string;
    matched_by: string[];
  }[];
  semantic: string;
  coverage_notes: string[];
}

export type ProcessingStatus = "queued" | "running" | "needs_input" | "completed" | "partial" | "failed" | "canceled";

export interface ProcessingJob {
  id: string;
  case_id: string;
  evidence_id: string;
  job_type: "whatsapp_export" | "document_text";
  status: ProcessingStatus;
  options: Record<string, string>;
  result: Record<string, unknown>;
  needs_input: {
    field: string;
    question: string;
    basis: string;
    explanation: string;
    samples: string[];
    choices: string[];
  } | null;
  attempts: number;
  error_code: string | null;
  error_detail: string | null;
  cancel_requested_at: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ProcessingJobDetail extends ProcessingJob {
  derived_evidence: { id: string; title: string; kind: string; page_part: string | null; size_bytes: number; content_type: string }[];
  derived_evidence_total: number;
  observation_count: number;
}

export interface ImportAccepted {
  evidence: Evidence;
  job: ProcessingJob;
  filename_sanitized: boolean;
  duplicate_of: string[];
}

export interface TimelineItem {
  observation_id: string;
  observation_type: string;
  time: string | null;
  time_basis: "event_time" | "source_published_at" | "local_time_without_timezone" | "collected_at_only";
  local_time: string | null;
  timestamp_text: string | null;
  collected_at: string;
  source_published_at: string | null;
  summary: string | null;
  source_label: string | null;
  source_object_id: string | null;
  entity_id: string | null;
  entity_name: string | null;
  evidence_id: string | null;
  evidence_title: string | null;
  acquisition_method: string | null;
  connector_id: string | null;
  connector_run_id: string | null;
  location: Record<string, number> | null;
  notes: string[];
}

export type TimelineSection = "dated" | "local_time_only" | "undated";

export interface TimelineData {
  section: TimelineSection;
  items: TimelineItem[];
  total: number;
  limit: number;
  offset: number;
  sections: Record<TimelineSection, number>;
}

export interface Comparison {
  entities: {
    id: string;
    display_name: string;
    entity_type: string;
    origin: string;
    observation_count: number;
    linked_evidence_count: number;
    event_time_span: string[] | null;
    published_span: string[] | null;
    collected_span: string[] | null;
    coverage: {
      source: string;
      acquisition_method: string;
      collection_mode: string | null;
      observations: number;
      connector_runs: { id: string; outcome: string | null; stopped_reason: string | null; finished_at: string | null }[];
      first_collected_at: string;
      last_collected_at: string;
    }[];
  }[];
  identifiers: { kind: "shared" | "only_one" | "conflicting_platform_id"; identifier_type: string; platform: string | null; values: string[]; entity_ids: string[] }[];
  relationships: {
    id: string;
    source_entity_id: string;
    source_name: string;
    target_entity_id: string;
    target_name: string;
    predicate: string;
    origin: string;
    review_status: string;
    reference_count: number;
    between_compared: boolean;
  }[];
  shared_neighbours: { entity_id: string; display_name: string; connections: Record<string, string[]> }[];
  changes: {
    entity_id: string;
    observation_type: string;
    source_object_id: string | null;
    field: string;
    previous: string | null;
    current: string | null;
    previous_collected_at: string;
    current_collected_at: string;
    previous_evidence_id: string | null;
    current_evidence_id: string | null;
    note: string;
  }[];
  changes_truncated: boolean;
  absences: {
    entity_id: string;
    connector_id: string;
    later_run_outcome: string | null;
    later_run_stopped_reason: string | null;
    items: string[];
    items_total: number;
    interpretation: "not_observed_in_later_complete_collection" | "unknown_later_collection_incomplete";
    note: string;
  }[];
  conflicts: { kind: string; entity_ids: string[]; field: string; values: string[]; note: string; evidence_ids: string[] }[];
  unresolved: string[];
  merge_policy: string;
}

export interface ReportPreview {
  counts: Record<string, number>;
  redactions_applied: number;
  credential_like_values_removed: number;
  warnings: string[];
  size_bytes: number;
  html: string;
}

/** GET /api/v1/activity: recent work across the cases the user can open. */
export interface Activity {
  runs: QueryRun[];
  active_runs: number;
  processing_jobs: ProcessingJob[];
  active_processing_jobs: number;
  jobs_needing_input: ProcessingJob[];
  cases: { id: string; title: string; status: string }[];
}

// -- Phase 5: team access, audit ------------------------------------------------------------------

export interface Member {
  user_id: string;
  username: string;
  account_role: "administrator" | "analyst" | "viewer";
  account_active: boolean;
  membership_role: CaseRole;
  effective_role: CaseRole;
  added_at: string;
  updated_at: string;
}

export interface Account {
  id: string;
  username: string;
  role: "administrator" | "analyst" | "viewer";
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  locked_until: string | null;
  case_count: number;
  analyst_case_count: number;
}

export interface AccountChange {
  account: Account;
  cases_without_active_analyst: string[];
}

export interface DirectoryAccount {
  id: string;
  username: string;
  role: Account["role"];
}

export interface DirectoryCase {
  id: string;
  title: string;
  status: CaseStatus;
  created_at: string;
  member_count: number;
  active_analyst_count: number;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_type: "user" | "service" | "system";
  actor_user_id: string | null;
  actor_label: string;
  case_id: string | null;
  action: string;
  outcome: "succeeded" | "denied" | "failed";
  target_type: string | null;
  target_id: string | null;
  correlation_id: string | null;
  details: Record<string, unknown>;
}

// -- Phase 5: monitoring, budgets, change detection -----------------------------------------------

export type MonitorStatus = "enabled" | "paused" | "disabled";

export interface MonitorSchedule {
  kind: "interval" | "daily" | "weekly";
  every_minutes?: number;
  time?: string;
  weekdays?: number[];
}

export interface BudgetUsage {
  scope_type: "case" | "monitor" | "query_run";
  metric: "requests" | "provider_units";
  period: "day" | "week" | "month" | "run";
  period_start: string;
  period_end: string | null;
  limit_units: number;
  reserved_units: number;
  consumed_units: number;
  estimated_units: number;
  remaining_units: number;
  exhausted: boolean;
  denied_requests: number;
}

export interface ChangeSummary {
  change_set_id: string;
  connector_id: string;
  status: ChangeSetStatus;
  counts: Record<string, number>;
}

export interface Occurrence {
  id: string;
  kind: "scheduled" | "manual";
  scheduled_for: string;
  status: "dispatched" | "skipped";
  skip_reason: string | null;
  missed_slots: number;
  config_version: number;
  query_run_id: string | null;
  run_status: RunStatus | null;
  run_error_code: string | null;
  connector_outcomes: (string | null)[];
  changes: ChangeSummary[];
  dispatched_by: string;
  created_at: string;
}

export interface Monitor {
  id: string;
  case_id: string;
  saved_query_id: string;
  saved_query_name: string;
  name: string;
  description: string;
  status: MonitorStatus;
  status_reason: string | null;
  status_changed_at: string;
  schedule: MonitorSchedule;
  timezone: string;
  missed_run_policy: "run_latest" | "skip";
  connector_ids: string[];
  scope: { max_pages: number; max_items_per_page: number };
  limits: { max_requests_per_run: number; max_items_per_run: number; max_run_seconds: number };
  budget: { period: "day" | "week" | "month"; max_requests: number; max_provider_units: number | null };
  retention: { keep_last_runs: number | null; max_age_days: number | null };
  notify: {
    on_change: boolean;
    on_failure: boolean;
    on_budget_exhausted: boolean;
    on_completion: boolean;
    recipients: "case_analysts" | "all_members";
  };
  config_version: number;
  collects_live: boolean;
  query_changed: boolean;
  authorized_by: string | null;
  next_run_at: string | null;
  last_scheduled_for: string | null;
  consecutive_failures: number;
  active_run_id: string | null;
  last_occurrence: Occurrence | null;
  budget_usage: BudgetUsage[];
  actions: { code: string; message: string }[];
  created_at: string;
  updated_at: string;
}

export type ChangeSetStatus = "baseline_established" | "no_meaningful_change" | "changes_detected" | "unknown" | "baseline_incompatible";
export type ChangeKind = "new" | "changed" | "not_observed" | "conflicting" | "unknown";

export interface ChangeSet {
  id: string;
  monitor_id: string | null;
  occurrence_id: string | null;
  query_run_id: string;
  connector_run_id: string;
  connector_id: string;
  connector_version: string;
  baseline_query_run_id: string | null;
  baseline_connector_run_id: string | null;
  status: ChangeSetStatus;
  coverage_complete: boolean;
  baseline_coverage_complete: boolean | null;
  counts: Record<ChangeKind, number>;
  limitations: string[];
  truncated: boolean;
  created_at: string;
}

export interface ChangeEvent {
  id: string;
  kind: ChangeKind;
  observation_type: string;
  source_object_id: string | null;
  field: string | null;
  previous_value: string | null;
  current_value: string | null;
  entity_id: string | null;
  previous_observation_id: string | null;
  current_observation_id: string | null;
  previous_evidence_id: string | null;
  current_evidence_id: string | null;
  previous_evidence_available: boolean | null;
  current_evidence_available: boolean | null;
  note: string;
}

export interface ChangeSetDetail extends ChangeSet {
  events: Page<ChangeEvent>;
}

export interface CaseBudgets {
  budgets: { metric: "requests" | "provider_units"; period: "day" | "week" | "month"; limit_units: number }[];
  usage: BudgetUsage[];
  measurement: string;
}

// -- Phase 5: notifications ---------------------------------------------------------------------

export interface AppNotification {
  id: string;
  case_id: string | null;
  case_title: string | null;
  monitor_id: string | null;
  event_id: string;
  event_type: "change_detected" | "action_required" | "budget_exhausted" | "run_completed";
  severity: "info" | "warning" | "action_required";
  title: string;
  body: string;
  link: string | null;
  created_at: string;
  read_at: string | null;
}

export interface Destination {
  id: string;
  name: string;
  url: string;
  host: string;
  enabled: boolean;
  event_types: string[];
  max_per_minute: number;
  signing: boolean;
  subscriptions: number;
  last_delivery_at: string | null;
  last_status: string | null;
  consecutive_failures: number;
  adapter_enabled: boolean;
  created_at: string;
  updated_at: string;
  signing_secret?: string | null;
}

export interface PayloadPreview {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
  notes: string[];
}

export interface Delivery {
  id: string;
  case_id: string;
  event_id: string;
  event_type: string;
  status: "pending" | "delivered" | "failed" | "blocked";
  attempts: number;
  next_attempt_at: string;
  last_error_code: string | null;
  last_response_status: number | null;
  delivered_at: string | null;
  created_at: string;
}

export interface AvailableDestination {
  id: string;
  name: string;
  host: string;
  event_types: string[];
}

export interface Subscription {
  id: string;
  monitor_id: string;
  destination_id: string;
  destination_name: string;
  destination_host: string;
  destination_enabled: boolean;
  event_types: string[];
  created_at: string;
}

// -- Phase 5: retention and exchange ------------------------------------------------------------

export interface RetentionPolicy {
  active: boolean;
  collected_results_max_age_days: number | null;
  imported_evidence_max_age_days: number | null;
  version: number;
  activated_at: string | null;
  last_applied_at: string | null;
  monitor_rules: { monitor_id: string; name: string; keep_last_runs: number | null; max_age_days: number | null }[];
}

export interface RetentionPreview {
  executions: number;
  imported_originals: number;
  evidence_records: number;
  stored_bytes: number;
  observations: number;
  relationship_references: number;
  index_chunks: number;
  ai_citations_affected: number;
  protected_baselines: number;
  deferred: Record<string, number>;
  oldest: string | null;
  newest: string | null;
  not_removed: string[];
}

export interface RetentionJob {
  id: string;
  trigger: "scheduled" | "manual" | "activation";
  status: "queued" | "running" | "completed" | "failed";
  policy_version: number;
  attempts: number;
  removed: Record<string, number>;
  deferred: Record<string, number>;
  progress_note: string | null;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface StixReport {
  counts: Record<string, number>;
  excluded: Record<string, number>;
  credential_values_removed: number;
  truncated: boolean;
  lossy: string[];
  objects: number;
  media_type: string;
}

export interface StixImportResult {
  evidence_id: string | null;
  already_imported: boolean;
  bundle_id: string | null;
  objects: number;
  created: Record<string, number>;
  reused: Record<string, number>;
  skipped: Record<string, number>;
  skipped_objects: { id: string; type: string; reason: string }[];
  warnings: string[];
}
