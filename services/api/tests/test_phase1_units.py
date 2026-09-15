from __future__ import annotations

import json

import pytest

from app.connectors.base import ConnectorError, FetchRequest
from app.connectors.fixture import SCENARIOS, FixtureConnector
from app.entities import normalize
from app.entities.models import IdentifierType
from app.evidence.importing import ImportRejectedError, sanitize_filename, validate_content
from app.evidence.models import EvidenceKind
from app.exports.csv_safety import neutralize
from app.queries.models import ConnectorOutcome

# -- identifier normalization ------------------------------------------------------------------


def test_domain_normalization_handles_turkish_idn() -> None:
    assert normalize.normalize_domain("Örnek.COM.tr.") == "xn--rnek-4qa.com.tr"
    with pytest.raises(normalize.IdentifierError):
        normalize.normalize_domain("not a domain")


def test_email_normalization_keeps_comparison_key_only() -> None:
    assert (
        normalize.normalize_email("Şule.YILMAZ@Örnek.com.tr") == "şule.yilmaz@xn--rnek-4qa.com.tr"
    )


def test_url_ip_and_phone_normalization() -> None:
    assert (
        normalize.normalize_url("HTTPS://Example.ORG:443/a?b=1#frag") == "https://example.org/a?b=1"
    )
    assert normalize.normalize_url("http://[2001:db8::1]:8080/x") == "http://[2001:db8::1]:8080/x"
    assert normalize.normalize_ip("2001:0db8:0000::0001") == "2001:db8::1"
    assert normalize.normalize_phone("+90 (212) 555 01 01") == "+902125550101"
    assert normalize.normalize_phone("0212 555 01 01") == "02125550101"
    with pytest.raises(normalize.IdentifierError):
        normalize.normalize_url("javascript:alert(1)")


def test_username_normalization_folds_turkish_i_without_merging_decisions() -> None:
    assert normalize.normalize_identifier(
        IdentifierType.USERNAME, "IŞIK"
    ) == normalize.normalize_identifier(IdentifierType.USERNAME, "ışık")


def test_control_characters_are_rejected() -> None:
    with pytest.raises(normalize.IdentifierError):
        normalize.normalize_identifier(IdentifierType.OTHER, "abc\x00def")


# -- import validation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\system32\\cmd.exe", "cmd.exe"),
        (".hidden", "hidden"),
        ("report‮gpj.exe", "reportgpj.exe"),
        ("normal-dosya_ğüşiöç.txt", "normal-dosya_ğüşiöç.txt"),
        ("   ", "unnamed"),
        ("/", "unnamed"),
    ],
)
def test_filenames_are_reduced_to_safe_display_names(raw: str, expected: str) -> None:
    assert sanitize_filename(raw).value == expected


def test_overlong_filenames_keep_extension_within_byte_limit() -> None:
    result = sanitize_filename("ş" * 300 + ".json")
    assert result.value is not None
    assert result.value.endswith(".json")
    assert len(result.value.encode("utf-8")) <= 255
    assert result.changed


@pytest.mark.parametrize(
    ("kind", "content", "code"),
    [
        (EvidenceKind.TEXT, b"", "empty_content"),
        (EvidenceKind.TEXT, b"\xff\xfe\x00bad", "invalid_encoding"),
        (EvidenceKind.TEXT, b"text\x00with nul", "binary_content"),
        (EvidenceKind.JSON, b"{not json", "invalid_json"),
        (EvidenceKind.JSON, b'{"value": NaN}', "invalid_json"),
        (EvidenceKind.JSON, b"[" * 100 + b"]" * 100, "json_too_deep"),
    ],
)
def test_malformed_imports_are_rejected_with_codes(
    kind: EvidenceKind, content: bytes, code: str
) -> None:
    with pytest.raises(ImportRejectedError) as caught:
        validate_content(kind, content, max_json_depth=64)
    assert caught.value.code == code


def test_valid_turkish_text_and_json_are_accepted() -> None:
    validate_content(EvidenceKind.TEXT, "Çalışma notu: İstanbul".encode(), max_json_depth=64)
    validate_content(EvidenceKind.JSON, json.dumps({"şehir": "İzmir"}).encode(), max_json_depth=64)


# -- CSV safety --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ['=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)", "\t=cmd", "  =1+1", chr(0xFF1D) + "1+1"],
)
def test_formula_like_cells_are_neutralized(value: str) -> None:
    assert neutralize(value).startswith("'")


def test_ordinary_cells_are_unchanged() -> None:
    assert neutralize("İstanbul") == "İstanbul"
    assert neutralize(-5) == "-5"
    assert neutralize(None) == ""
    assert neutralize(True) == "true"


# -- synthetic fixture connector ---------------------------------------------------------------


def _request(
    scenario: str = "findings", page: int = 0, attempt: int = 1, input_type: str = "username"
) -> FetchRequest:
    return FetchRequest(
        input_type=input_type,
        input_value="şule.yılmaz",
        parameters={"scenario": scenario},
        page_index=page,
        attempt=attempt,
        max_items_per_page=5,
    )


def test_fixture_is_deterministic_and_labelled_synthetic() -> None:
    connector = FixtureConnector()
    first = connector.fetch_page(_request())
    second = connector.fetch_page(_request())
    assert first.raw_payload == second.raw_payload
    assert first.raw_payload["synthetic"] is True
    assert "SYNTHETIC" in first.raw_payload["label"]
    for item in first.items:
        assert item.platform.endswith(".example")
        assert item.linked_domain is not None
        assert item.linked_domain.endswith(".example")
        assert item.profile_reference.startswith("https://synthetic-")


@pytest.mark.parametrize(
    ("scenario", "page", "attempt", "outcome"),
    [
        ("failure", 0, 1, ConnectorOutcome.UNAVAILABLE),
        ("authentication_required", 0, 1, ConnectorOutcome.AUTHENTICATION_REQUIRED),
        ("access_denied", 0, 1, ConnectorOutcome.ACCESS_DENIED),
        ("parse_error", 0, 1, ConnectorOutcome.PARSE_ERROR),
        ("rate_limited", 0, 1, ConnectorOutcome.RATE_LIMITED),
        ("flaky", 1, 1, ConnectorOutcome.UNAVAILABLE),
        ("partial", 1, 3, ConnectorOutcome.UNAVAILABLE),
    ],
)
def test_fixture_scenarios_raise_prd_outcomes(
    scenario: str, page: int, attempt: int, outcome: ConnectorOutcome
) -> None:
    with pytest.raises(ConnectorError) as caught:
        FixtureConnector().fetch_page(_request(scenario, page, attempt))
    assert caught.value.outcome == outcome


def test_fixture_retry_scenarios_recover_and_no_findings_is_explicit() -> None:
    connector = FixtureConnector()
    assert connector.fetch_page(_request("flaky", 1, 2)).items
    assert connector.fetch_page(_request("rate_limited", 0, 2)).items
    empty = connector.fetch_page(_request("no_findings"))
    assert empty.items == []
    assert empty.has_more is False
    with pytest.raises(ConnectorError) as caught:
        connector.fetch_page(_request(input_type="phone"))
    assert caught.value.outcome == ConnectorOutcome.UNSUPPORTED


def test_fixture_rejects_unknown_scenarios() -> None:
    with pytest.raises(ValueError, match="unknown fixture scenario"):
        FixtureConnector().validate("username", "x", {"scenario": "real_target"})
    assert "slow" in SCENARIOS
