"""Deterministic synthetic AI provider for tests and demonstrations.

It is not a language model. Plans are keyword rules, answers are extractive sentence picks and
embeddings are feature hashes. Everything it produces is labelled synthetic in the API and UI,
and it is only used when ``TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture`` is configured.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Literal

from app.ai.models import ProcessingLocation
from app.ai.providers.base import (
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    ModelInventory,
    Usage,
    require_grant,
)
from app.ai.text import fold_for_search

FIXTURE_GENERATION_MODEL = "synthetic-extractive-v1"
FIXTURE_EMBEDDING_MODEL = "synthetic-hash-embedding-v1"
FIXTURE_DIMENSIONS = 256

_WORD = re.compile(r"[\w.@:/-]+", re.UNICODE)
# A sentence ends at . ! or ? followed by an uppercase letter, a quote or the end of the line, so
# domain names (ornek.example) and abbreviations (A.Ş. tarafından) stay inside their sentence.
_SENTENCE = re.compile(
    r"[^\s][^\n]*?(?:[.!?]+(?=\s+[A-ZÇĞİÖŞÜ\"'(\[]|[ \t]*(?:\n|$))|(?=\n)|$)", re.MULTILINE
)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_STOPWORDS = frozenset(
    fold_for_search(word)
    for word in [
        "what",
        "which",
        "who",
        "when",
        "where",
        "does",
        "did",
        "have",
        "with",
        "from",
        "that",
        "this",
        "there",
        "about",
        "case",
        "evidence",
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "how",
        "many",
        "much",
        "according",
        "show",
        "tell",
        "list",
        "hangi",
        "nedir",
        "kim",
        "zaman",
        "nerede",
        "neden",
        "icin",
        "olan",
        "ile",
        "gore",
        "kanit",
        "kanitlara",
        "vaka",
        "mi",
        "mu",
        "kac",
        "tane",
        "var",
        "midir",
    ]
)


def _tokens(text: str) -> list[str]:
    return [token.strip(".:/-") for token in _WORD.findall(fold_for_search(text))]


def _content_words(text: str) -> set[str]:
    return {token for token in _tokens(text) if len(token) >= 4 and token not in _STOPWORDS}


class FixtureEmbeddingProvider:
    name = "synthetic_fixture"
    location = ProcessingLocation.FIXTURE
    model = FIXTURE_EMBEDDING_MODEL
    synthetic = True

    def embed(self, texts: list[str], *, purpose: Literal["document", "query"]) -> EmbeddingResult:
        vectors = []
        for text in texts:
            vector = [0.0] * FIXTURE_DIMENSIONS
            for token in _tokens(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % FIXTURE_DIMENSIONS
                vector[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return EmbeddingResult(
            vectors=vectors, usage=Usage(source="unavailable"), model=FIXTURE_EMBEDDING_MODEL
        )

    def inventory(self) -> ModelInventory:
        return ModelInventory(reachable=True, models={FIXTURE_EMBEDDING_MODEL: "fixture-v1"})


class FixtureGenerationProvider:
    name = "synthetic_fixture"
    location = ProcessingLocation.FIXTURE
    model = FIXTURE_GENERATION_MODEL
    synthetic = True

    def generate_json(self, request: GenerationRequest) -> GenerationResult:
        require_grant(request, ProcessingLocation.FIXTURE)
        context = request.context
        if request.task == "plan":
            data = self._plan(context)
        elif request.task == "relationship_suggestions":
            data = self._suggest(context)
        else:
            data = self._answer(context, summary=request.task == "summary")
        return GenerationResult(
            data=data, usage=Usage(source="unavailable"), model=FIXTURE_GENERATION_MODEL
        )

    # -- planning -----------------------------------------------------------------------------

    @staticmethod
    def _plan(context: Any) -> dict[str, Any]:
        question = fold_for_search(str(context.get("question", "")))
        calls: list[dict[str, Any]] = []
        counting = any(
            key in question
            for key in ("how many", "kac ", "sayisi", "count of", "number of records")
        )
        published = any(key in question for key in ("publish", "yayin"))
        prefix = "published" if published else "collected"
        dates = _DATE.findall(question)
        date_args: dict[str, str] = {}
        if len(dates) >= 2 and any(key in question for key in ("between", "arasinda")):
            date_args = {f"{prefix}_from": dates[0], f"{prefix}_to": dates[1]}
        elif dates and any(key in question for key in ("after", "since", "sonra", "itibaren")):
            date_args[f"{prefix}_from"] = dates[0]
        elif dates and any(key in question for key in ("before", "until", "once")):
            date_args[f"{prefix}_to"] = dates[0]
        if counting:
            if any(key in question for key in ("relationship", "iliski")):
                arguments: dict[str, Any] = {}
                if "unreviewed" in question or "incelenmemis" in question:
                    arguments["review_status"] = "unreviewed"
                if "accepted" in question or "kabul" in question:
                    arguments["review_status"] = "accepted"
                calls.append({"tool": "count_relationships", "arguments": arguments})
            elif any(key in question for key in ("entit", "varlik")):
                arguments = {}
                for entity_type in ("domain", "organization", "username", "platform_account", "ip"):
                    if entity_type.replace("_", " ") in question:
                        arguments["entity_type"] = entity_type
                        break
                calls.append({"tool": "count_entities", "arguments": arguments})
            elif any(key in question for key in ("run", "execution", "calistirma")):
                calls.append({"tool": "count_query_runs", "arguments": {}})
            elif any(key in question for key in ("observation", "gozlem")):
                calls.append({"tool": "count_observations", "arguments": {}})
            else:
                arguments = dict(date_args)
                if "import" in question or "ice aktar" in question:
                    arguments["acquisition_method"] = "authorized_import"
                if "json" in question:
                    arguments["kind"] = "json"
                calls.append({"tool": "count_evidence", "arguments": arguments})
        elif date_args and any(
            key in question for key in ("which evidence", "list", "hangi kanit")
        ):
            calls.append({"tool": "list_evidence", "arguments": date_args})
        if any(
            key in question
            for key in ("coverage", "incomplete", "partial", "eksik", "kapsam", "complete")
        ):
            calls.append({"tool": "connector_coverage", "arguments": {}})
        return {"tool_calls": calls, "search_query": str(context.get("question", ""))}

    # -- answers and summaries ----------------------------------------------------------------

    @staticmethod
    def _best_sentence(text: str, words: set[str]) -> tuple[int, str]:
        best = (0, "")
        for match in _SENTENCE.finditer(text):
            sentence = match.group(0).strip()
            if len(sentence) < 8:
                continue
            score = len(words & _content_words(sentence))
            if score > best[0]:
                best = (score, sentence[:300])
        return best

    def _answer(self, context: Any, *, summary: bool) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for tool in context.get("tools", []):
            result = tool.get("result") or {}
            if isinstance(result, dict) and isinstance(result.get("count"), int):
                claims.append(
                    {
                        "text": (
                            f"{tool.get('description', 'Matching records')}: {result['count']}."
                        ),
                        "kind": "count",
                        "citations": [{"ref": tool["ref"], "quote": ""}],
                    }
                )
        blocks = list(context.get("evidence", []))
        if summary:
            for block in blocks[:5]:
                sentence = next(
                    (
                        m.group(0).strip()
                        for m in _SENTENCE.finditer(block["text"])
                        if len(m.group(0).strip()) >= 8
                    ),
                    "",
                )[:300]
                if sentence:
                    claims.append(
                        {
                            "text": sentence,
                            "kind": "fact",
                            "citations": [{"ref": block["ref"], "quote": sentence}],
                        }
                    )
        else:
            words = _content_words(str(context.get("question", "")))
            scored = sorted(
                ((self._best_sentence(block["text"], words), block) for block in blocks),
                key=lambda item: -item[0][0],
            )
            threshold = max(2, math.ceil(len(words) * 0.5)) if words else 99
            for (score, sentence), block in scored[:2]:
                if score >= threshold and sentence:
                    claims.append(
                        {
                            "text": sentence,
                            "kind": "fact",
                            "citations": [{"ref": block["ref"], "quote": sentence}],
                        }
                    )
        if not claims:
            return {
                "status": "insufficient_evidence",
                "claims": [
                    {
                        "text": "The indexed case evidence does not answer this question.",
                        "kind": "insufficient",
                        "citations": [],
                    }
                ],
                "limitations": [],
            }
        return {"status": "answered", "claims": claims, "limitations": []}

    # -- relationship suggestions -----------------------------------------------------------

    @staticmethod
    def _suggest(context: Any) -> dict[str, Any]:
        suggestions: list[dict[str, Any]] = []
        entities = list(context.get("entities", []))
        for block in context.get("evidence", []):
            for match in _SENTENCE.finditer(block["text"]):
                sentence = match.group(0).strip()
                folded = fold_for_search(sentence)
                present = [
                    entity
                    for entity in entities
                    if any(
                        fold_for_search(term) and fold_for_search(term) in folded
                        for term in [entity["name"], *entity.get("identifiers", [])]
                    )
                ]
                for position, source in enumerate(present):
                    for target in present[position + 1 :]:
                        if source["ref"] == target["ref"]:
                            continue
                        suggestions.append(
                            {
                                "source": source["ref"],
                                "target": target["ref"],
                                "predicate": "associated_with",
                                "rationale": "Both entities are named in the same sentence.",
                                "citations": [{"ref": block["ref"], "quote": sentence[:300]}],
                            }
                        )
        return {"suggestions": suggestions[:10]}
