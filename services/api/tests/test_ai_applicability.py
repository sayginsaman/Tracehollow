"""Claim applicability, support of the asserted value, and conflict disclosure.

These are the three defects the frozen evaluation run exposed, reproduced with synthetic claims
(no dataset identifiers, names or expected answers):

1. an answer built from evidence about a neighbouring subject;
2. a fact that asserts something its cited passage does not state, including an absence;
3. two records that give different values for the same attribute, reported as separate settled
   facts instead of a disclosed contradiction — while a genuine change over time must not be
   labelled a contradiction.

Every check is deterministic: the model declares what each claim is about, and the server
compares subjects, values and times itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.ai.retrieval import RetrievedChunk
from app.ai.tools import ToolResult
from app.ai.validation import validate_answer


def _chunk(text: str, *, title: str = "Record", published: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        evidence_id=uuid.uuid4(),
        evidence_sha256="a" * 64,
        chunk_index=0,
        kind="text",
        text=text,
        char_start=0,
        char_end=len(text),
        json_locations=None,
        evidence_title=title,
        acquisition_method="authorized_import",
        collected_at=datetime(2026, 9, 10, tzinfo=UTC),
        source_published_at=datetime.fromisoformat(published) if published else None,
        source_published_at_original=published,
        source_reference=None,
        connector_id=None,
        query_run_id=None,
    )


def _claim(text: str, **about: Any) -> dict[str, Any]:
    citations = about.pop("citations", [])
    kind = about.pop("kind", "fact")
    answers = about.pop("answers_question", True)
    return {
        "text": text,
        "kind": kind,
        "answers_question": answers,
        "about": {
            "subject": about.get("subject", ""),
            "attribute": about.get("attribute", ""),
            "value": about.get("value", ""),
            "as_of": about.get("as_of", ""),
        },
        "citations": citations,
    }


def _validate(
    question: str,
    claims: list[dict[str, Any]],
    evidence: dict[str, RetrievedChunk],
    tools: dict[str, ToolResult] | None = None,
) -> Any:
    return validate_answer(
        {"claims": claims, "limitations": []},
        question=question,
        evidence=evidence,
        tools=tools or {},
        secrets=[],
    )


# -- defect 1: evidence about a neighbouring subject ---------------------------------------------


def test_claim_about_a_neighbouring_subject_does_not_answer_the_question() -> None:
    certificate = _chunk('/subject_common_name: "shop.example.test"\n/issuer: "Test Authority"')
    answer = _validate(
        "Which certificate authority issued a certificate for portal.example.test?",
        [
            _claim(
                "The certificate authority for shop.example.test is Test Authority.",
                subject="shop.example.test",
                attribute="certificate authority",
                value="Test Authority",
                citations=[{"ref": "E1", "quote": '/issuer: "Test Authority"'}],
            )
        ],
        {"E1": certificate},
    )
    # The statement is true of another host, so it cannot answer this question.
    assert answer.status == "insufficient_evidence"
    assert [claim.kind for claim in answer.claims] == ["fact", "insufficient"]
    assert answer.claims[0].answers_question is False
    assert answer.claims[0].applicability == "other_subject"
    assert any("another subject" in note for note in answer.server_notes)


def test_a_subject_that_matches_the_question_still_answers_it() -> None:
    record = _chunk('/subject_common_name: "portal.example.test"\n/issuer: "Test Authority"')
    answer = _validate(
        "Which certificate authority issued a certificate for portal.example.test?",
        [
            _claim(
                "The certificate authority for portal.example.test is Test Authority.",
                subject="portal.example.test",
                attribute="certificate authority",
                value="Test Authority",
                citations=[{"ref": "E1", "quote": '/issuer: "Test Authority"'}],
            )
        ],
        {"E1": record},
    )
    assert answer.status == "answered"
    assert answer.claims[0].answers_question is True


def test_a_question_without_identifiers_never_drops_claims_for_subject_mismatch() -> None:
    record = _chunk("The chief financial officer is Dilek Arslan.")
    answer = _validate(
        "Who is the chief financial officer?",
        [
            _claim(
                "The chief financial officer is Dilek Arslan.",
                subject="the company",
                attribute="chief financial officer",
                value="Dilek Arslan",
                citations=[{"ref": "E1", "quote": "chief financial officer is Dilek Arslan"}],
            )
        ],
        {"E1": record},
    )
    assert answer.status == "answered"


# -- defect 2: the cited passage does not state the asserted value -------------------------------


def test_a_fact_whose_value_is_absent_from_its_citation_is_removed() -> None:
    memo = _chunk("Operations memo: the staging host is used only for internal load tests.")
    answer = _validate(
        "Which former vendor did the vendor memo name?",
        [
            _claim(
                "The vendor memo did not name a former vendor.",
                subject="the vendor memo",
                attribute="former vendor",
                value="none",
                citations=[{"ref": "E1", "quote": "used only for internal load tests"}],
            )
        ],
        {"E1": memo},
    )
    assert answer.status == "insufficient_evidence"
    assert [removed.reason for removed in answer.removed] == ["value_not_in_cited_evidence"]
    assert [claim.kind for claim in answer.claims] == ["insufficient"]
    assert "does not support" in " ".join(answer.server_notes)


def test_a_value_the_citation_states_is_kept_even_when_worded_differently() -> None:
    report = _chunk("Kayıt özeti: alan adı 2026-09-01 tarihinde tescil edildi.")
    answer = _validate(
        "Kayıt hangi tarihte yapıldı?",
        [
            _claim(
                "Alan adı 1 Eylül 2026 tarihinde tescil edildi.",
                subject="alan adı",
                attribute="tescil tarihi",
                value="2026-09-01",
                citations=[{"ref": "E1", "quote": "2026-09-01 tarihinde tescil edildi"}],
            )
        ],
        {"E1": report},
    )
    assert answer.status == "answered"
    assert answer.claims[0].kind == "fact"


def test_counts_from_database_results_are_not_treated_as_missing_values() -> None:
    tool = ToolResult(ref="T1", name="count_evidence", arguments={}, result={"count": 14})
    answer = _validate(
        "How many evidence records are in this case?",
        [
            _claim(
                "There are 14 evidence records in this case.",
                kind="count",
                subject="this case",
                attribute="evidence records",
                value="14",
                citations=[{"ref": "T1", "quote": ""}],
            )
        ],
        {},
        {"T1": tool},
    )
    assert answer.status == "answered"
    assert answer.claims[0].kind == "count"


# -- defect 3: contradictions and changes over time ----------------------------------------------


def test_two_records_with_different_values_become_one_disclosed_conflict() -> None:
    first = _chunk("Inventory: the portal is operated by North Cloud.", title="Inventory")
    second = _chunk("Contractor email: the portal is served by South Data.", title="Email")
    answer = _validate(
        "Which company hosts the portal?",
        [
            _claim(
                "The portal is operated by North Cloud.",
                subject="the portal",
                attribute="hosting company",
                value="North Cloud",
                citations=[{"ref": "E1", "quote": "operated by North Cloud"}],
            ),
            _claim(
                "The portal is served by South Data.",
                subject="the portal",
                attribute="hosting company",
                value="South Data",
                citations=[{"ref": "E2", "quote": "served by South Data"}],
            ),
        ],
        {"E1": first, "E2": second},
    )
    assert [claim.kind for claim in answer.claims] == ["conflict"]
    conflict = answer.claims[0]
    assert {citation.chunk.evidence_id for citation in conflict.citations if citation.chunk} == {
        first.evidence_id,
        second.evidence_id,
    }
    assert "North Cloud" in conflict.text
    assert "South Data" in conflict.text
    assert "Inventory" in conflict.text
    assert "Email" in conflict.text
    assert conflict.difference_type == "disagreement"
    assert answer.status in ("answered", "partially_answered")
    assert any("disagreement" in note for note in answer.server_notes)


def test_different_values_at_different_times_are_a_change_not_a_contradiction() -> None:
    first = _chunk("2026-09-05: the host resolved to 203.0.113.7.", published="2026-09-05")
    second = _chunk("2026-09-07: the host resolved to 198.51.100.23.", published="2026-09-07")
    answer = _validate(
        "What address does the host resolve to?",
        [
            _claim(
                "On 2026-09-05 the host resolved to 203.0.113.7.",
                subject="the host",
                attribute="resolved address",
                value="203.0.113.7",
                as_of="2026-09-05",
                citations=[{"ref": "E1", "quote": "resolved to 203.0.113.7"}],
            ),
            _claim(
                "On 2026-09-07 the host resolved to 198.51.100.23.",
                subject="the host",
                attribute="resolved address",
                value="198.51.100.23",
                as_of="2026-09-07",
                citations=[{"ref": "E2", "quote": "resolved to 198.51.100.23"}],
            ),
        ],
        {"E1": first, "E2": second},
    )
    # Both observations stay visible and cited, but the difference is typed as a change, and the
    # answer never calls it a disagreement.
    assert [claim.kind for claim in answer.claims] == ["conflict"]
    disclosed = answer.claims[0]
    assert disclosed.difference_type == "change_over_time"
    assert "203.0.113.7" in disclosed.text
    assert "198.51.100.23" in disclosed.text
    assert "2026-09-05" in disclosed.text
    assert "2026-09-07" in disclosed.text
    assert "change over time rather than a disagreement" in disclosed.text
    assert len(disclosed.citations) == 2
    assert any("change over time" in note for note in answer.server_notes)


def test_the_same_value_from_two_records_is_not_a_conflict() -> None:
    first = _chunk("Registry: registered by Northern Registrar.", title="Registry")
    second = _chunk("WHOIS-style record: registrar is Northern Registrar.", title="WHOIS")
    answer = _validate(
        "Who is the registrar?",
        [
            _claim(
                "The registrar is Northern Registrar.",
                subject="the domain",
                attribute="registrar",
                value="Northern Registrar",
                citations=[{"ref": "E1", "quote": "registered by Northern Registrar"}],
            ),
            _claim(
                "The WHOIS-style record names Northern Registrar.",
                subject="the domain",
                attribute="registrar",
                value="Northern Registrar",
                citations=[{"ref": "E2", "quote": "registrar is Northern Registrar"}],
            ),
        ],
        {"E1": first, "E2": second},
    )
    assert [claim.kind for claim in answer.claims] == ["fact", "fact"]
    assert answer.status == "answered"


# -- status of the final, user-visible answer ----------------------------------------------------


def test_a_supported_fact_about_another_attribute_leaves_the_question_unanswered() -> None:
    announcement = _chunk("Duyuru: portal 6 Eylül 2026 tarihinde kullanıma açıldı.")
    answer = _validate(
        "Portal hangi tarihte kapatıldı?",
        [
            _claim(
                "Portal 6 Eylül 2026 tarihinde kullanıma açıldı.",
                subject="portal",
                attribute="açılış tarihi",
                value="6 Eylül 2026",
                answers_question=False,
                citations=[{"ref": "E1", "quote": "6 Eylül 2026 tarihinde kullanıma açıldı"}],
            ),
            _claim(
                "Kapatılma tarihi belirtilmemiştir.",
                kind="insufficient",
                answers_question=False,
            ),
        ],
        {"E1": announcement},
    )
    # The supported context stays visible, but the question itself is unanswered.
    assert answer.status == "insufficient_evidence"
    assert [claim.kind for claim in answer.claims] == ["fact", "insufficient"]
    assert answer.claims[0].answers_question is False


def test_a_partially_answerable_question_keeps_its_supported_part() -> None:
    record = _chunk("The portal opened on 2026-09-06 and is run by North Cloud.")
    answer = _validate(
        "When did the portal open and who audited it?",
        [
            _claim(
                "The portal opened on 2026-09-06.",
                subject="the portal",
                attribute="opening date",
                value="2026-09-06",
                citations=[{"ref": "E1", "quote": "opened on 2026-09-06"}],
            ),
            _claim(
                "The case material does not show who audited it.",
                kind="insufficient",
                answers_question=False,
            ),
        ],
        {"E1": record},
    )
    assert answer.status == "partially_answered"
    assert [claim.kind for claim in answer.claims] == ["fact", "insufficient"]


def test_a_side_the_model_kept_as_context_is_still_disclosed() -> None:
    inventory = _chunk("Inventory: the portal is operated by North Cloud.", title="Inventory")
    email = _chunk("Contractor email: the portal is served by South Data.", title="Email")
    answer = _validate(
        "Which company hosts the portal?",
        [
            _claim(
                "The portal is operated by North Cloud.",
                subject="the portal",
                attribute="hosting company",
                value="North Cloud",
                citations=[{"ref": "E1", "quote": "operated by North Cloud"}],
            ),
            _claim(
                "The portal is served by South Data.",
                subject="the portal",
                attribute="hosting company",
                value="South Data",
                answers_question=False,
                citations=[{"ref": "E2", "quote": "served by South Data"}],
            ),
        ],
        {"E1": inventory, "E2": email},
    )
    assert [claim.kind for claim in answer.claims] == ["conflict"]
    assert answer.claims[0].answers_question is True
    assert answer.claims[0].difference_type == "disagreement"
    assert len(answer.claims[0].citations) == 2


def test_records_published_at_different_times_without_a_stated_period_stay_undetermined() -> None:
    # Publication dates say when a record was written, not which period its value covers, so the
    # server discloses the difference without deciding that one of the two readings is right.
    first = _chunk(
        "The portal is hosted by North Cloud.", title="Inventory", published="2026-03-02"
    )
    second = _chunk("The portal is hosted by South Data.", title="Audit", published="2026-09-08")
    answer = _validate(
        "Which company hosts the portal?",
        [
            _claim(
                "The portal is hosted by North Cloud.",
                subject="the portal",
                attribute="hosting company",
                value="North Cloud",
                citations=[{"ref": "E1", "quote": "hosted by North Cloud"}],
            ),
            _claim(
                "The portal is hosted by South Data.",
                subject="the portal",
                attribute="hosting company",
                value="South Data",
                citations=[{"ref": "E2", "quote": "hosted by South Data"}],
            ),
        ],
        {"E1": first, "E2": second},
    )
    disclosed = answer.claims[0]
    assert disclosed.kind == "conflict"
    assert disclosed.difference_type == "undetermined"
    assert "may be a change rather than a disagreement" in disclosed.text
    assert "2026-03-02" in disclosed.text
    assert "2026-09-08" in disclosed.text
    assert len(disclosed.citations) == 2


def test_the_same_property_under_two_labels_is_still_one_conflict() -> None:
    # The model named the same property "hosting_company" from one record and "hosting_location"
    # from the other, which used to hide the disagreement behind two separate facts.
    inventory = _chunk("Envanter: portal, Polar Bulut tarafından işletiliyor.", title="Envanter")
    email = _chunk("Contractor email: the portal is served by Mavi Veri Merkezi.", title="Email")
    answer = _validate(
        "Which company hosts the portal?",
        [
            _claim(
                "The hosting company for the portal is Polar Bulut.",
                subject="the portal",
                attribute="hosting_company",
                value="Polar Bulut",
                citations=[{"ref": "E1", "quote": "Polar Bulut tarafından işletiliyor"}],
            ),
            _claim(
                "The hosting location for the portal is Mavi Veri Merkezi.",
                subject="the portal",
                attribute="hosting_location",
                value="Mavi Veri Merkezi",
                answers_question=False,
                citations=[{"ref": "E2", "quote": "served by Mavi Veri Merkezi"}],
            ),
        ],
        {"E1": inventory, "E2": email},
    )
    assert [claim.kind for claim in answer.claims] == ["conflict"]
    assert answer.claims[0].difference_type == "disagreement"
    assert len(answer.claims[0].citations) == 2


def test_an_address_is_never_compared_with_a_company_name() -> None:
    inventory = _chunk("Inventory: the portal runs on 192.0.2.10, operated by Polar Bulut.")
    answer = _validate(
        "Where is the portal hosted?",
        [
            _claim(
                "The portal's hosting company is Polar Bulut.",
                subject="the portal",
                attribute="hosting_company",
                value="Polar Bulut",
                citations=[{"ref": "E1", "quote": "operated by Polar Bulut"}],
            ),
            _claim(
                "The portal's hosting address is 192.0.2.10.",
                subject="the portal",
                attribute="hosting_address",
                value="192.0.2.10",
                citations=[{"ref": "E1", "quote": "runs on 192.0.2.10"}],
            ),
        ],
        {"E1": inventory},
    )
    # Two properties of one record, not two sides of a disagreement.
    assert [claim.kind for claim in answer.claims] == ["fact", "fact"]


def test_two_different_properties_that_share_only_a_generic_word_are_not_a_conflict() -> None:
    registry = _chunk("Registry: the domain was registered on 2026-09-01.", title="Registry")
    certificate = _chunk("Certificate: valid until 2026-12-10.", title="Certificate")
    answer = _validate(
        "What dates does the case material give for the domain?",
        [
            _claim(
                "The domain was registered on 2026-09-01.",
                subject="the domain",
                attribute="registration date",
                value="2026-09-01",
                citations=[{"ref": "E1", "quote": "registered on 2026-09-01"}],
            ),
            _claim(
                "The certificate is valid until 2026-12-10.",
                subject="the domain",
                attribute="certificate validity date",
                value="2026-12-10",
                citations=[{"ref": "E2", "quote": "valid until 2026-12-10"}],
            ),
        ],
        {"E1": registry, "E2": certificate},
    )
    # Sharing the word "date" is not sharing a property; merging these would invent a conflict.
    assert [claim.kind for claim in answer.claims] == ["fact", "fact"]


def test_a_claim_that_attaches_the_asked_subject_to_another_hosts_record_is_removed() -> None:
    # The passage is about three other host names. Naming the asked-for host in "about.subject"
    # must not turn it into evidence for that host, even though the value is in the passage.
    certificate = _chunk(
        '/subject_common_name: "ornek.example"\n'
        '/subject_alternative_names/0: "www.ornek.example"\n'
        '/issuer: "Örnek Test CA"'
    )
    answer = _validate(
        "Which certificate authority issued a certificate for destek.ornek.example?",
        [
            _claim(
                "The certificate for destek.ornek.example was issued by Örnek Test CA.",
                subject="destek.ornek.example",
                attribute="certificate authority",
                value="Örnek Test CA",
                citations=[{"ref": "E1", "quote": '/issuer: "Örnek Test CA"'}],
            )
        ],
        {"E1": certificate},
    )
    assert answer.status == "insufficient_evidence"
    assert [removed.reason for removed in answer.removed] == ["subject_not_in_cited_evidence"]
    assert [claim.kind for claim in answer.claims] == ["insufficient"]


def test_a_subject_the_passage_does_name_is_kept() -> None:
    certificate = _chunk('/subject_common_name: "destek.ornek.example"\n/issuer: "Örnek Test CA"')
    answer = _validate(
        "Which certificate authority issued a certificate for destek.ornek.example?",
        [
            _claim(
                "The certificate for destek.ornek.example was issued by Örnek Test CA.",
                subject="destek.ornek.example",
                attribute="certificate authority",
                value="Örnek Test CA",
                citations=[{"ref": "E1", "quote": '/issuer: "Örnek Test CA"'}],
            )
        ],
        {"E1": certificate},
    )
    assert answer.status == "answered"
    assert not answer.removed


def test_a_subject_without_an_identifier_is_left_to_the_reviewer() -> None:
    memo = _chunk("The board approved the budget on 2026-09-04.")
    answer = _validate(
        "When did the board approve the budget?",
        [
            _claim(
                "The board approved the budget on 2026-09-04.",
                subject="the board",
                attribute="approval date",
                value="2026-09-04",
                citations=[{"ref": "E1", "quote": "approved the budget on 2026-09-04"}],
            )
        ],
        {"E1": memo},
    )
    assert answer.status == "answered"
    assert not answer.removed
