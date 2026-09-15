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
}

export interface CaseDetail extends CaseSummary {
  counts: CaseCounts;
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
  kind: "text" | "json";
  title: string;
  original_filename: string | null;
  content_type: string;
  size_bytes: number;
  sha256: string;
  acquisition_method: "authorized_import" | "synthetic_fixture";
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
  duplicate_of: string[];
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
}

export interface ConnectorDescriptor {
  connector_id: string;
  version: string;
  display_name: string;
  synthetic: boolean;
  description: string;
  supported_input_types: string[];
  collection_mode: string;
  credential_requirements: string;
  coverage: string;
  max_pages: number;
  max_items_per_page: number;
  timeout_seconds: number;
  retry_max_attempts: number;
  retryable_outcomes: string[];
  output_schema: string;
  cost_model: string | null;
  last_live_verification: string | null;
  parameters: { scenario?: Record<string, string> };
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
  evidence_count: number;
  observation_count: number;
  dispatch_status: string | null;
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
