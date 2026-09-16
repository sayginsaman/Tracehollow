# ADR 0005: Evidence-grounded AI with an explicit pipeline, local models and server-side validation

- Status: accepted
- Date: 2026-09-16
- Phase: 3

## Context

PRD Phase 3 requires chunking, versioned embeddings, pgvector indexing and hybrid retrieval; a local
Ollama provider and an optional cloud provider; case Q&A with source citations, deterministic read
tools, summaries and relationship suggestions; usage records and analyst review; and a synthetic
evaluation set. Acceptance requires exact supporting evidence, numeric agreement with database
queries, explicit insufficient-evidence answers, zero cross-case leakage and invalid citations,
local-only enforcement, injection resistance and a workspace that works with AI disabled.

Phase 2 (live public-source collection) has not been implemented. Phase 3 therefore operates on
authorized imports and the labelled synthetic fixture connector; the complete collection-to-AI
milestone cannot be claimed until Phase 2 exists.

The PRD asks for one of Haystack, LlamaIndex or a minimal explicit pipeline, chosen after testing
the requirements.

## Decision

### Orchestration: a minimal explicit pipeline

No AI framework is installed. The pipeline is plain Python with Pydantic-validated contracts:

1. **Plan** (model, structured output): choose registered read tools with arguments and a search
   query.
2. **Tools** (server): validate each call against a strict argument model and run fixed,
   parameterized SQLAlchemy queries in a read-only transaction; the case id is bound by the server.
3. **Retrieve** (server): case-scoped hybrid retrieval.
4. **Generate** (model, structured output): claims with kinds (`fact`, `count`, `inference`,
   `conflict`, `insufficient`), citations (`E#` evidence blocks with a verbatim quote, `T#`
   database results) and limitations.
5. **Validate** (server): keep only citations to context that was actually provided whose quotes
   are found in the block; drop factual claims without a verified citation and count claims whose
   numbers are not in the cited result; redact configured secret values; turn unsupported answers
   into explicit insufficient-evidence answers.

Each step is small enough to test deterministically, and every security property depends on
server code rather than on the model following instructions.

### Providers and processing policy

- `GenerationProvider` and `EmbeddingProvider` protocols with three implementations: local Ollama
  (`/api/chat` with a JSON-schema `format`, `/api/embed`), optional Anthropic Messages generation
  (`output_config.format` structured outputs) and a deterministic **synthetic fixture** provider
  for tests and demos that is labelled everywhere it appears.
- Anthropic offers no embeddings API, so embeddings are always local.
- `policy.authorize_generation` is the only place that issues a processing grant. It is called
  immediately before every model request and checks that AI is enabled, the case mode allows the
  requested location and the case policy version is unchanged. There is no fallback from local to
  cloud or back. Providers refuse requests whose grant does not match their location.
- Defaults: generation `qwen3:8b`, embeddings `qwen3-embedding:0.6b` (both Apache-2.0,
  multilingual). They are configuration, not dependencies; the stack starts without them.

### Credentials and network

- The cloud API key is supplied as a Compose secret file (`secrets/cloud_ai_api_key`, empty by
  default) and mounted only into `api` (to know it is configured) and `ai-worker` (to use it). It is
  never stored in PostgreSQL, sent to the browser or logged.
- A separate `ai-worker` service consumes the `tracehollow-ai` queue and is the only service that
  makes model requests. It is attached to a dedicated `ai-egress` network; `worker` and `dispatcher`
  stay on the internal `data` network, and `api` never calls a model (it shares only the `edge`
  network with `web`). The Ollama base URL is operator configuration
  (`TRACEHOLLOW_AI_OLLAMA_BASE_URL`), validated at startup and never derived from case data, so it
  does not interact with future SSRF controls for collected URLs.

### Index and retrieval

- `document_chunks` store exact slices of the decoded original text (character offsets) or, for
  JSON, a flattened `pointer: value` rendering with RFC 6901 pointers per line. Chunks keep the
  evidence SHA-256 and chunking version.
- Vectors live in `chunk_embeddings` keyed by `(chunk, embedding_profile)`; a profile is provider,
  model, model digest, chunking version and indexing version. Only one profile is active; retrieval
  never compares vectors across profiles. Records indexed under another profile or chunking version
  are reported as `stale`.
- The `vector` column has no fixed dimension; a check constraint ties each row to its profile's
  dimensions. Semantic search is an exact cosine scan filtered by case and profile before ranking.
  No approximate index is created yet: case sizes in scope are small, and filtered ANN indexes lose
  recall.
- Full-text search uses the `simple` configuration over accent- and Turkish-`i`-folded text;
  exact identifiers (domains, URLs, emails, IPs, hashes, usernames) are extracted into an array with
  a GIN index. Results are merged with reciprocal rank fusion, at most three chunks per evidence
  record so disagreeing sources are not crowded out.
- Indexing is dispatched through the outbox (`case_index` aggregates) with leases, bounded batches,
  retries with backoff, cancellation and failure codes; the dispatcher schedules pending work
  (including evidence imported while AI was disabled and data migrated from Phase 1).

### Runs, authorization and records

- `ai_runs` are claimed with leases like query executions and re-dispatched by the dispatcher when a
  worker disappears; repeated loss fails the run (`worker_lost`).
- Before each stage, before each model request and in the result transaction, the worker re-checks
  case status, the requester's active membership, AI availability and the policy version. A failed
  check stops the run without storing output.
- Conversations are visible to case members (like notes). Follow-up questions include up to three
  earlier validated answers as context only; they can never be cited.
- Runs store provider, model, processing location, prompt template version, retrieval summary,
  tool calls (including rejected ones), coverage, validation report and provider-reported usage.
  Cost is recorded as unknown; Tracehollow does not estimate prices.
- Citations store the chunk, evidence id, evidence hash, the verified quote and its exact source
  offsets or JSON pointer. The citation endpoint re-reads and re-hashes the original evidence
  before showing the passage.

### Relationship suggestions

Suggestions may only connect existing entities, must cite a verified quote from a passage that
mentions both entities, and are stored as `ai_suggestion` relationships in `unreviewed` state with
supporting evidence references. They never create or merge entities. Analysts review them with the
existing decision history. No confidence score is produced.

### Evidence deletion

Phase 3 adds deliberate deletion of imported evidence (typed title confirmation). The stored
original is removed first, then the record; chunks, vectors, links and index state cascade, and
quoted text in earlier citations is purged so those citations report `source_deleted`. Evidence
collected by executions stays with its execution history.

### pgvector installation

pgvector is not a trusted extension. A one-shot `db-extensions` Compose job creates it as the
PostgreSQL superuser before `migrate`; the migration creates it only when the role may, otherwise it
stops with instructions. Backups exclude the superuser-owned extension and restores recreate it.

## Alternatives considered

- **Haystack or LlamaIndex:** both provide retrievers and prompt pipelines, but the hard requirements
  here are server-side policy checks before every call, typed read-only tools, quote verification and
  evidence-hash-backed citations, none of which they provide. Adding one would introduce a large
  dependency tree and a second abstraction over the same few calls. Rejected for Phase 3; revisit if
  document processing (Phase 4) needs their loaders.
- **Native model tool calling in a loop:** lets the model iterate, but makes behaviour depend on
  provider-specific tool semantics and is harder to bound. A single planning step with validated
  calls is sufficient for counts and filters.
- **Letting the model write SQL:** forbidden by the PRD. Rejected.
- **Storing provider keys encrypted in the database:** needs a key-management scheme outside the
  database; a secret file achieves "server-side, never in the database" with less machinery.
- **Cloud embeddings:** would send all evidence text to a provider during indexing, not just
  retrieved excerpts at question time. Not offered.
- **Streaming answers over SSE:** bounded polling keeps authorization re-checks and result
  validation before anything is shown; partially generated, unvalidated text is never displayed.
- **HNSW or IVFFlat indexes:** faster at scale but approximate, and case filtering after
  approximate search loses recall. Deferred until measured case sizes require it.

## Consequences

- Answer quality depends on the local model; the pipeline guarantees provenance and refusals, not
  correctness. A human review of claim support (PRD acceptance criterion 5) is still required.
- Local generation takes tens of seconds per question on the verification laptop (see the measured
  evaluation timings in `docs/testing/ai-evaluation/`); the UI shows stages and allows cancellation.
- A cancel request cannot interrupt a model request already in flight; its output is discarded when
  it returns.
- Changing the embedding model marks existing vectors stale until the index is rebuilt; keyword and
  identifier search keep working in the meantime.
- Evidence text in the prompt is only protected structurally (nonce-delimited blocks and no write or
  network capabilities); a model can still be misled by hostile text into wrong claims, which the
  citation checks and the insufficient-evidence fallback limit but do not eliminate.

## Amendment (2026-09-15 follow-up): answer template and validation changes

The first model-backed runs showed three failure modes that were not model quality alone
(`docs/testing/ai-evaluation/`): unnecessary abstention although the evidence was retrieved and
in the prompt, disagreeing sources reported as separate settled facts, and claims that hedged
against what their own citation stated.

Changes, all general (no question, answer or dataset-specific rule):

1. **The answer schema puts `status` last** (`claims`, `limitations`, `status`). Structured
   decoding follows property order, so the model committed to a status before writing any claim
   and then produced claims its own limitations contradicted. Deciding after the claims removed
   the unnecessary abstentions in the development set.
2. **Answer template rules** (`answer-v8`): answer from what a record states without requiring
   outside confirmation; attribute assertions to their record; word an insufficient claim as what
   the material does not show, never as an absence; answer questions about a named record only
   from that record; keep coverage notes in `limitations` instead of turning them into claims;
   include only claims that answer the question; JSON evidence is read as `/path: value` lines.
   Three invented example claim shapes are included, with the instruction never to reuse their
   content.
3. **Validation**: a `conflict` claim needs verified citations from at least two different
   records, otherwise it is removed as a one-sided fragment; claims kept with only some of their
   citations verified are disclosed in a server note; removed claims keep their text in the
   validation report so a reviewer can judge the filter itself.

**Rejected: a second model pass to label conflicts.** A bounded consistency check (only when an
answer had two or more fact claims citing different records) asked the model which statements gave
incompatible answers, and the server merged those into one `conflict` claim with both citations.
On the development set it merged four groups, of which three were wrong: two statements agreeing
on a registration date, an announcement date paired with a certificate validity date, and an
unrelated pair. A mislabelled conflict is more misleading than a missing label, so the check was
removed (`prompt-version conflict-v1`, dropped). With `answer-v8` the model states the
disagreeing sources as separate attributed facts citing each record, so both sides stay visible
and cited; labelling them as one conflict remains a known weakness of this model.

## Second amendment (2026-09-15): the server decides what an answer is about

The `answer-v8` run left three defects that the prompt could not fix, because each needed a
comparison the model was being asked to make and getting wrong:

1. questions whose evidence does not exist were answered from a **neighbouring subject** — a
   sibling host name, a similar username, an adjacent date (`q31`, `h07`, `h08`);
2. a claim was accepted when its citation resolved, although the cited passage did not state the
   claim's value — a **valid citation reference was being read as factual support**;
3. **disagreeing records stayed separate settled facts**: neither the model nor the server ever
   said the sources differ (0 of 4 conflict questions).

The change moves the judgement from the model to the server, and narrows what the model is asked
for to something it can report rather than decide.

**The model declares, the server checks.** Every claim now carries `about`
(`subject`, `attribute`, `value`, `as_of`) and `answers_question`; the global `status` is gone
(`answer-v11`). The model's job is to say what each record states and what the claim is about. The
server then performs four deterministic checks (`app/ai/validation.py`):

- **value grounding** — the asserted value must appear in the cited passage (accent- and
  case-folded, with date-format equivalence) or in the cited database result. A fact whose value
  its citation does not contain is removed as `value_not_in_cited_evidence`, however valid the
  citation reference is. This is the check that stops an absence being asserted from a passage
  that simply says something else;
- **subject applicability** — identifiers are extracted from the question and from the claim's
  subject and compared **normalised but exactly**, per identifier type. `shop.example.test` does
  not answer a question about `portal.example.test`, and `emre_kocak` is not `emre_koc`. A claim
  about another subject is kept and cited but marked `other_subject`, forced to
  `answers_question=false`, and reported in a server note. When the question carries no identifier
  of the claim's type, applicability is `unspecified` and nothing is dropped, so ordinary
  questions are unaffected;
- **difference disclosure** — supported facts are grouped by normalised subject and attribute.
  Two or more distinct values from two or more distinct records are merged into one `conflict`
  claim citing every side, with each value labelled by its record. Context claims take part in the
  grouping, so a side cannot disappear by being demoted;
- **status** — computed last, from what survived: no supported claim that answers the question
  gives `insufficient_evidence`; removals, context claims or an insufficient claim give
  `partially_answered`.

**A difference is not automatically a contradiction.** `difference_type` is `change_over_time`
when every side states a period and the periods differ, `disagreement` when the periods are the
same or absent, and `undetermined` when the records give no period but were published at different
times. Publication date is metadata about a record, not a statement about when its value held, so
it is never enough to declare a change; the undetermined case says so in the answer instead of
picking a side.

**Structured records are compared without the model.** `find_conflicting_records` returns
relationships that share a source and predicate but point at different targets, and it is run
automatically for identifiers found in the question. Where the case already holds structured
observations, the disagreement is found by SQL, not by interpretation.

This differs from the rejected second model pass in what is delegated: the model is no longer
asked which statements conflict, only what each one is about. The grouping, the comparison and the
labelling are the server's, and every step is recorded in the validation report.

## Verification

`services/api/tests/test_ai_*.py` (unit, indexing, Q&A and deterministic evaluation suites),
`scripts/verify-phase3.sh` (stack, including the existing-database upgrade and backup drill),
`scripts/ai-eval.sh` (model-backed evaluation) and `apps/web/src/components/ai/ai-views.test.tsx`
plus `apps/web/e2e/phase3-ai.spec.ts`. Results are recorded in `docs/STATUS.md` and
`docs/testing/ai-evaluation/`.
