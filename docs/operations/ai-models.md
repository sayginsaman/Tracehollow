# AI models: installation, configuration and data controls

Tracehollow's AI features (indexing, cited case Q&A, summaries and relationship suggestions) are
optional. The workspace starts and works without any model: cases, evidence, entities, queries,
exports and deletion do not depend on AI. Design: [ADR 0005](../adr/0005-evidence-grounded-ai.md).

## How the pieces fit

| Component | Where it runs | What it does |
| --- | --- | --- |
| `ai-worker` (Compose service) | container, queue `tracehollow-ai` | Chunks and embeds evidence, runs questions, summaries and suggestions. The only service that talks to a model. |
| Ollama | on the host (or another machine you control) | Serves the local generation and embedding models. Not part of the Compose stack. |
| Cloud provider (optional) | Anthropic Messages API | Generation only, for cases an analyst explicitly allows. Never used for embeddings. |
| `dispatcher` | container | Schedules pending indexing, re-dispatches lost AI work, checks model availability every 10 minutes. |

Model endpoints are operator configuration. They are never taken from case data.

## Local models with Ollama

### Install and pull models

1. Install Ollama from <https://ollama.com/download> (verified with Ollama 0.34.0 on macOS).
2. Pull the default models:

   ```bash
   ollama pull qwen3:8b               # generation, 8.2B parameters, Q4_K_M, 5.2 GB download
   ollama pull qwen3-embedding:0.6b   # embeddings, 596M parameters, Q8_0, 639 MB download, 1024 dimensions
   ollama list
   ```

   Both models are published under the Apache-2.0 license. Model licenses are separate from
   Tracehollow's code license and from the licenses of any data you process.

3. Start the stack as usual (`docker compose up --build --detach --wait`). Open a case, select the
   **AI** tab and look at **Model providers** (press **Check now** to refresh). The case index
   starts filling as soon as the embedding model is reachable.

### Hardware

The verification machine was an Apple M3 Pro laptop with 36 GB of unified memory. On it,
`qwen3:8b` answered evaluation questions in tens of seconds each; the measured timings are in
[docs/testing/ai-evaluation](../testing/ai-evaluation/). No other hardware was tested, and
Tracehollow makes no performance claims for other machines. Memory in use as reported by
`ollama ps` on that machine (Ollama 0.34.0):

| Model | Context requested | Reported size |
| --- | --- | --- |
| `qwen3:8b` | 16 384 tokens (`TRACEHOLLOW_AI_NUM_CTX`) | 7.4 GB |
| `qwen3-embedding:0.6b` | 8 192 tokens (`TRACEHOLLOW_AI_EMBEDDING_NUM_CTX`) | 2.9 GB |
| `qwen3-embedding:0.6b` | 32 768 tokens (the model default, for comparison) | 5.8 GB |

Both models stay loaded for 10 minutes after their last use. Machines with less memory can lower the
context settings or use a smaller generation model (see below) at the cost of answer quality.

### Reaching Ollama from the containers

`ai-worker` connects to `TRACEHOLLOW_AI_OLLAMA_BASE_URL`, by default
`http://host.docker.internal:11434`. Compose maps `host.docker.internal` to the host gateway.

- **macOS and Windows (Docker Desktop):** works with Ollama's default loopback listener
  (verified on macOS only).
- **Linux Docker Engine (untested):** `host.docker.internal` resolves to the Docker bridge gateway,
  but Ollama listens on `127.0.0.1` by default, which containers cannot reach. Make Ollama listen on
  the bridge address (for example `OLLAMA_HOST=172.17.0.1:11434` in the Ollama service environment)
  and allow that port only from Docker networks in your firewall. Do not expose Ollama on
  `0.0.0.0` on an untrusted network: it has no authentication.
- **Ollama on another machine:** set `TRACEHOLLOW_AI_OLLAMA_BASE_URL=http://<address>:11434` in
  `.env`. Traffic is unencrypted HTTP; use only a network you trust, or a tunnel.

### Choosing other models

Set `TRACEHOLLOW_AI_GENERATION_MODEL` and `TRACEHOLLOW_AI_EMBEDDING_MODEL` in `.env`, pull the
models, then `docker compose up --detach`.

- The generation model must support JSON-schema structured output through Ollama's `format`
  parameter. Only `qwen3:8b` has been evaluated; rerun the evaluation (below) before relying on
  another model.
- Changing the embedding model (or Ollama replacing the model with a different digest) creates a
  new embedding profile. Records indexed with the previous profile are shown as **stale**; semantic
  retrieval ignores them until you press **Rebuild stale** on the case AI tab. Keyword and exact
  identifier search keep working meanwhile. Tracehollow never mixes vectors from different profiles.

## Optional cloud generation (Anthropic)

Cloud processing is off unless an operator configures it **and** an analyst allows it per case.

1. Put the API key in `secrets/cloud_ai_api_key` (one line; the file is git-ignored and mounted only
   into `api` and `ai-worker`):

   ```bash
   umask 077 && printf '%s' 'sk-ant-…' > secrets/cloud_ai_api_key
   ```

2. In `.env` set `TRACEHOLLOW_AI_CLOUD_PROVIDER=anthropic` and, if needed,
   `TRACEHOLLOW_AI_CLOUD_MODEL` (default `claude-sonnet-5`). Restart: `docker compose up --detach`.
3. In a case's **AI** tab choose **Cloud allowed**, confirm the acknowledgement and save. The
   processing indicator changes to show that cloud processing is permitted. Each question still
   asks whether to use the local or the cloud model.

What leaves the machine for a cloud request: the question, earlier validated answers in that
conversation, retrieved evidence excerpts and read-tool results for that case, and the fixed system
instructions. Not sent: whole evidence files, embeddings, credentials or other cases' data. The key is
never stored in PostgreSQL, returned by the API or written to logs.

Limitations:

- The integration follows Anthropic's documented Messages API (`POST /v1/messages`,
  `anthropic-version: 2023-06-01`, structured output with `output_config.format`) and is covered by
  tests with mocked responses shaped after that documentation. **It has not been exercised against the live API**
  because no key was available during verification.
- Usage (input and output tokens) is recorded as reported by the provider. Tracehollow does not
  estimate prices; cost is shown as unknown.
- There is no automatic fallback. If the cloud provider fails, the request fails with a specific
  error; it is not retried locally, and a failed local request is never sent to the cloud.

## Local-only enforcement

- New cases are **Local only**. The server refuses cloud requests for such cases (`409
  cloud_processing_not_allowed`) before anything is queued.
- The worker re-checks the case setting and its policy version immediately before every model
  request. Changing a case from **Cloud allowed** to **Local only** or **Off** cancels queued runs
  and stops running ones before their next model call; nothing further is sent.
- Embeddings are always computed locally, whatever the case setting.

## Disabling AI

- **One case:** choose **Off** on the case AI tab. No indexing or AI requests run for that case;
  existing conversations remain readable.
- **Whole installation:** set `TRACEHOLLOW_AI_ENABLED=false` in `.env` and run
  `docker compose up --detach`. AI endpoints that start work return `409 ai_disabled`, AI requests
  that were already queued are canceled with `ai_disabled` when the worker reaches them, indexing
  pauses, and the rest of the workspace is unaffected. Set it back to `true` to resume; evidence
  imported in the meantime is indexed then.
- To stop the worker entirely: `docker compose stop ai-worker` (requests stay queued until it
  returns).

Removing derived data: deleting an imported evidence record removes its chunks, vectors and index
state and purges quoted text from earlier citations; deleting a case removes all of its AI records,
including conversations (see [evidence-storage.md](evidence-storage.md)).

## Synthetic fixture provider (tests and demos)

`TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture` replaces Ollama with a deterministic keyword-based
stand-in (`synthetic-extractive-v1`, `synthetic-hash-embedding-v1`). It needs no model and is used
by the automated tests and `scripts/verify-phase3.sh`. Every answer it produces is labelled
**Synthetic model** in the UI and `synthetic_fixture` in the API. It is not a language model and its
answers say nothing about real model quality.

## Settings reference

| Variable | Default | Notes |
| --- | --- | --- |
| `TRACEHOLLOW_AI_ENABLED` | `true` | Installation-wide switch |
| `TRACEHOLLOW_AI_LOCAL_PROVIDER` | `ollama` | `ollama` or `synthetic_fixture` |
| `TRACEHOLLOW_AI_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | `http` or `https`, no credentials or query in the URL |
| `TRACEHOLLOW_AI_GENERATION_MODEL` | `qwen3:8b` | |
| `TRACEHOLLOW_AI_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Changing it marks existing vectors stale |
| `TRACEHOLLOW_AI_NUM_CTX` | `16384` | Context window requested from Ollama |
| `TRACEHOLLOW_AI_EMBEDDING_NUM_CTX` | `8192` | Embedding context; longer input fails with `provider_bad_request` instead of being truncated |
| `TRACEHOLLOW_AI_REQUEST_TIMEOUT_SECONDS` | `300` | Per local model request |
| `TRACEHOLLOW_AI_CLOUD_PROVIDER` | `none` | `none` or `anthropic` |
| `TRACEHOLLOW_AI_CLOUD_MODEL` | `claude-sonnet-5` | |
| `secrets/cloud_ai_api_key` | empty | Cloud API key file |

Further bounds (output tokens, context characters, retrieved passages, tool calls, active runs per
case, batch sizes, retries) are defined with their limits in `services/api/app/config.py` and can be
set through `TRACEHOLLOW_AI_*` variables in the backend service environment.

## Troubleshooting

| Shown error | Meaning and fix |
| --- | --- |
| `model_unavailable` | Ollama could not be reached. Start it (`ollama serve` or the desktop app) and check the base URL; on Linux see the listener note above. |
| `model_not_found` | The model is not installed in Ollama: `ollama pull <model>`. |
| `provider_timeout` | No response within the timeout. Large contexts on slower machines can exceed it; raise `TRACEHOLLOW_AI_REQUEST_TIMEOUT_SECONDS` (it must stay below the run lease) or use a smaller model. |
| `output_truncated` | The model hit the output limit. Ask a narrower question. |
| `invalid_model_output` | The model did not return the required JSON structure. Retry; if it persists, the model is not suitable. |
| `provider_auth_failed` | The cloud key was rejected. Replace `secrets/cloud_ai_api_key` and restart. |
| `provider_rate_limited` / `provider_overloaded` | Try again later. |
| Index entry `failed` with `invalid_encoding` / `invalid_json` / `too_many_chunks` | The record cannot be indexed as stored (or exceeds `TRACEHOLLOW_AI_MAX_CHUNKS_PER_EVIDENCE`). The evidence itself is unchanged. |
| Index entry `failed` with `provider_bad_request` (*input length exceeds the context length*) | A chunk is longer than the embedding context. Raise `TRACEHOLLOW_AI_EMBEDDING_NUM_CTX` or lower `TRACEHOLLOW_AI_CHUNK_TARGET_CHARS`, then **Retry failed and canceled**. |
| Index entry `failed` with `embedding_dimension_mismatch` | The embedding model changed dimensions under the same name. Press **Rebuild all**. |
| Answers keep saying *insufficient evidence* | Check the index counts (records still pending or stale are not searched semantically) and the coverage notes under the answer. |

Service logs: `docker compose logs ai-worker dispatcher`. Routine log lines carry run and case ids
and error codes; prompts, evidence text and keys are not logged (`scripts/verify-phase3.sh` scans the
service logs for secrets and seeded evidence text).

## Evaluating a model

`scripts/ai-eval.sh --providers configured` runs the versioned synthetic evaluation set against the
configured models in a disposable database and writes `results.json`, `summary.md` and a `review/`
package (one row per claim with its cited passages, one row per question). `--only q03,q24` runs a
subset while iterating. Question accuracy, answering and abstention, and citation validity are
reported separately; claim support needs the human review described in
[docs/testing/ai-evaluation](../testing/ai-evaluation/README.md). Automated checks and model-made
labels are not a substitute for it.
