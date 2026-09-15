"""Case-scoped hybrid retrieval.

Three retrievers run inside the database, each filtered to one case before ranking:

- lexical: PostgreSQL full-text search over accent/case-folded chunk text (``simple`` parser);
- identifier: exact matches on normalized identifiers (domains, URLs, emails, IPs, hashes,
  usernames) extracted from the query;
- semantic: exact cosine distance over vectors of the active embedding profile only.

Results are merged with reciprocal rank fusion. Only chunks of evidence whose index state is
``indexed`` with the current chunking version are eligible, so pending, failed, canceled and
deleted evidence never appears. Vectors from another embedding profile are never compared.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.chunking import CHUNKING_VERSION
from app.ai.text import extract_identifiers, fold_for_search
from app.ai.vector import to_literal

RRF_K = 60
MAX_CHUNKS_PER_EVIDENCE = 3
CANDIDATES_PER_RETRIEVER = 40
MAX_QUERY_CHARS = 1000

# Very common words that would otherwise match almost every chunk with the simple parser.
STOPWORDS = sorted(
    {
        fold_for_search(word)
        for word in [
            "the",
            "and",
            "for",
            "are",
            "was",
            "were",
            "how",
            "many",
            "much",
            "what",
            "which",
            "who",
            "whom",
            "when",
            "where",
            "why",
            "does",
            "did",
            "have",
            "has",
            "with",
            "from",
            "that",
            "this",
            "there",
            "their",
            "about",
            "into",
            "over",
            "under",
            "than",
            "then",
            "them",
            "they",
            "you",
            "your",
            "our",
            "case",
            "evidence",
            "show",
            "tell",
            "list",
            "please",
            "according",
            "bir",
            "ve",
            "ile",
            "icin",
            "gibi",
            "kadar",
            "olan",
            "olarak",
            "ama",
            "fakat",
            "hangi",
            "nedir",
            "kim",
            "kimin",
            "zaman",
            "nerede",
            "neden",
            "nasil",
            "kac",
            "tane",
            "mi",
            "mu",
            "midir",
            "var",
            "yok",
            "bu",
            "şu",
            "o",
            "ne",
            "da",
            "de",
            "ki",
            "vaka",
            "kanit",
        ]
    }
)


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    evidence_id: uuid.UUID
    evidence_sha256: str
    chunk_index: int
    kind: str
    text: str
    char_start: int | None
    char_end: int | None
    json_locations: list[dict[str, Any]] | None
    evidence_title: str
    acquisition_method: str
    collected_at: datetime
    source_published_at: datetime | None
    source_published_at_original: str | None
    source_reference: str | None
    connector_id: str | None
    query_run_id: uuid.UUID | None
    score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)
    similarity: float | None = None

    @property
    def synthetic(self) -> bool:
        return self.acquisition_method == "synthetic_fixture"

    def summary(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "evidence_id": str(self.evidence_id),
            "chunk_index": self.chunk_index,
            "score": round(self.score, 6),
            "ranks": self.ranks,
            "similarity": None if self.similarity is None else round(self.similarity, 4),
        }


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    retrievers: dict[str, dict[str, Any]]
    query_identifiers: list[str]

    def summary(self) -> dict[str, Any]:
        return {
            "retrievers": self.retrievers,
            "query_identifiers": self.query_identifiers,
            "chunks": [chunk.summary() for chunk in self.chunks],
            "chunking_version": CHUNKING_VERSION,
        }


def _lexical(db: Session, case_id: uuid.UUID, query: str, limit: int) -> list[uuid.UUID]:
    folded = fold_for_search(query)[:MAX_QUERY_CHARS]
    rows = db.execute(
        text(
            """
            WITH terms AS (
                SELECT DISTINCT lexeme
                FROM unnest(tsvector_to_array(to_tsvector('simple', :folded))) AS lexeme
                WHERE length(lexeme) > 1 AND NOT (lexeme = ANY(:stopwords))
                LIMIT 32
            ),
            q AS (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
                FROM terms
                HAVING count(*) > 0
            )
            SELECT c.id, ts_rank_cd(c.search_vector, q.query, 32) AS rank
            FROM q, document_chunks c
                JOIN evidence_index_states s ON s.evidence_id = c.evidence_id
    WHERE c.case_id = :case_id
      AND s.case_id = :case_id
      AND s.status = 'indexed'
      AND s.chunking_version = :chunking_version
      AND c.chunking_version = :chunking_version
              AND c.search_vector @@ q.query
            ORDER BY rank DESC, c.id
            LIMIT :limit
            """
        ),
        {
            "folded": folded,
            "stopwords": STOPWORDS,
            "case_id": case_id,
            "chunking_version": CHUNKING_VERSION,
            "limit": limit,
        },
    ).all()
    return [row[0] for row in rows]


def _identifier(
    db: Session, case_id: uuid.UUID, identifiers: list[str], limit: int
) -> list[uuid.UUID]:
    if not identifiers:
        return []
    rows = db.execute(
        text(
            """
            SELECT c.id,
                   cardinality(ARRAY(
                       SELECT unnest(c.identifiers) INTERSECT SELECT unnest(CAST(:ids AS varchar[]))
                   )) AS hits
            FROM document_chunks c
                JOIN evidence_index_states s ON s.evidence_id = c.evidence_id
    WHERE c.case_id = :case_id
      AND s.case_id = :case_id
      AND s.status = 'indexed'
      AND s.chunking_version = :chunking_version
      AND c.chunking_version = :chunking_version
              AND c.identifiers && CAST(:ids AS varchar[])
            ORDER BY hits DESC, c.chunk_index, c.id
            LIMIT :limit
            """
        ),
        {
            "ids": identifiers,
            "case_id": case_id,
            "chunking_version": CHUNKING_VERSION,
            "limit": limit,
        },
    ).all()
    return [row[0] for row in rows]


def _semantic(
    db: Session,
    case_id: uuid.UUID,
    vector: list[float],
    profile_id: uuid.UUID,
    limit: int,
) -> list[tuple[uuid.UUID, float]]:
    rows = db.execute(
        text(
            """
            SELECT c.id, 1 - (e.embedding <=> CAST(:vector AS vector)) AS similarity
            FROM chunk_embeddings e
            JOIN document_chunks c ON c.id = e.chunk_id
                JOIN evidence_index_states s ON s.evidence_id = c.evidence_id
    WHERE c.case_id = :case_id
      AND s.case_id = :case_id
      AND s.status = 'indexed'
      AND s.chunking_version = :chunking_version
      AND c.chunking_version = :chunking_version
              AND e.case_id = :case_id
              AND e.profile_id = :profile_id
              AND s.profile_id = :profile_id
              AND e.dimensions = :dimensions
            ORDER BY e.embedding <=> CAST(:vector AS vector), c.id
            LIMIT :limit
            """
        ),
        {
            "vector": to_literal(vector),
            "dimensions": len(vector),
            "case_id": case_id,
            "profile_id": profile_id,
            "chunking_version": CHUNKING_VERSION,
            "limit": limit,
        },
    ).all()
    return [(row[0], float(row[1])) for row in rows]


def _load(db: Session, case_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, RetrievedChunk]:
    if not ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT c.id, c.evidence_id, c.evidence_sha256, c.chunk_index, c.kind, c.text,
                   c.char_start, c.char_end, c.json_locations, e.title, e.acquisition_method,
                   e.collected_at, e.source_published_at, e.source_published_at_original,
                   e.source_reference, e.connector_id, e.query_run_id
            FROM document_chunks c
            JOIN evidence_objects e ON e.id = c.evidence_id AND e.case_id = c.case_id
            WHERE c.case_id = :case_id AND c.id = ANY(:ids)
            """
        ),
        {"case_id": case_id, "ids": ids},
    ).all()
    return {
        row[0]: RetrievedChunk(
            chunk_id=row[0],
            evidence_id=row[1],
            evidence_sha256=row[2],
            chunk_index=row[3],
            kind=row[4],
            text=row[5],
            char_start=row[6],
            char_end=row[7],
            json_locations=row[8],
            evidence_title=row[9],
            acquisition_method=row[10],
            collected_at=row[11],
            source_published_at=row[12],
            source_published_at_original=row[13],
            source_reference=row[14],
            connector_id=row[15],
            query_run_id=row[16],
        )
        for row in rows
    }


def retrieve(
    db: Session,
    case_id: uuid.UUID,
    query: str,
    *,
    top_k: int,
    query_vector: list[float] | None = None,
    profile_id: uuid.UUID | None = None,
    semantic_unavailable_reason: str | None = None,
) -> RetrievalResult:
    query = query[:MAX_QUERY_CHARS]
    identifiers = extract_identifiers(query)
    lexical = _lexical(db, case_id, query, CANDIDATES_PER_RETRIEVER)
    identifier = _identifier(db, case_id, identifiers, CANDIDATES_PER_RETRIEVER)
    semantic: list[tuple[uuid.UUID, float]] = []
    retrievers: dict[str, dict[str, Any]] = {
        "lexical": {"used": True, "candidates": len(lexical)},
        "identifier": {"used": bool(identifiers), "candidates": len(identifier)},
    }
    if query_vector is not None and profile_id is not None:
        semantic = _semantic(db, case_id, query_vector, profile_id, CANDIDATES_PER_RETRIEVER)
        retrievers["semantic"] = {"used": True, "candidates": len(semantic)}
    else:
        retrievers["semantic"] = {
            "used": False,
            "reason": semantic_unavailable_reason or "no_active_embedding_profile",
        }

    scores: dict[uuid.UUID, float] = {}
    ranks: dict[uuid.UUID, dict[str, int]] = {}
    similarities = dict(semantic)
    for name, ordered in (
        ("lexical", lexical),
        ("identifier", identifier),
        ("semantic", [chunk_id for chunk_id, _ in semantic]),
    ):
        # Exact identifier matches count double: they are the strongest signal we have.
        weight = 2.0 if name == "identifier" else 1.0
        for position, chunk_id in enumerate(ordered, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + position)
            ranks.setdefault(chunk_id, {})[name] = position

    ordered_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], str(chunk_id)))
    loaded = _load(db, case_id, ordered_ids[: top_k * 4])
    selected: list[RetrievedChunk] = []
    per_evidence: dict[uuid.UUID, int] = {}
    for chunk_id in ordered_ids:
        chunk = loaded.get(chunk_id)
        if chunk is None:
            continue
        if per_evidence.get(chunk.evidence_id, 0) >= MAX_CHUNKS_PER_EVIDENCE:
            continue
        chunk.score = scores[chunk_id]
        chunk.ranks = ranks[chunk_id]
        chunk.similarity = similarities.get(chunk_id)
        per_evidence[chunk.evidence_id] = per_evidence.get(chunk.evidence_id, 0) + 1
        selected.append(chunk)
        if len(selected) >= top_k:
            break
    return RetrievalResult(chunks=selected, retrievers=retrievers, query_identifiers=identifiers)
