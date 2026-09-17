"""WhatsApp export parsing (PRD Phase 4 acceptance criterion 3).

All exports below are synthetic. They cover Android and iOS layouts, day-first, month-first and
year-first dates, 12- and 24-hour clocks, multiline messages, system events, Turkish text and
12-hour markers, ambiguous dates, unknown and explicit timezones, and attachment markers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.imports.whatsapp import WhatsAppParseError, parse_export

NNBSP = "\u202f"  # narrow no-break space newer exports put before AM/PM
LRM = "\u200e"  # left-to-right mark iOS puts before notices and media placeholders


def test_android_day_first_24h_turkish_text_multiline_and_offsets() -> None:
    text = (
        "31.12.2021 23:58 - Mesajlar ve aramalar uçtan uca şifrelidir.\n"
        "31.12.2021 23:59 - Şule Yılmaz: Görüşme İstanbul'da, saat 10:00'da\n"
        "ikinci satır: çok önemli\n"
        "01.01.2022 00:01 - +90 555 000 00 00: Tamam, ığüşöç\n"
    )
    result = parse_export(text, timezone="Europe/Istanbul")

    assert result.date_order.order == "day_first"
    assert result.date_order.basis == "inferred"
    assert result.date_order.proof_line == 1
    assert result.clock == "24h"
    system, multiline, phone_label = result.messages
    assert system.kind == "system"
    assert system.sender_label is None
    assert multiline.sender_label == "Şule Yılmaz"
    assert multiline.text == "Görüşme İstanbul'da, saat 10:00'da\nikinci satır: çok önemli"
    assert (multiline.line_start, multiline.line_end) == (2, 3)
    # Offsets point at the exact original characters, header and continuation included.
    assert text[multiline.char_start : multiline.char_end] == (
        "31.12.2021 23:59 - Şule Yılmaz: Görüşme İstanbul'da, saat 10:00'da\n"
        "ikinci satır: çok önemli"
    )
    assert multiline.timestamp_text == "31.12.2021 23:59"
    assert multiline.local_datetime == datetime(2021, 12, 31, 23, 59)  # noqa: DTZ001
    assert multiline.utc_datetime == datetime(2021, 12, 31, 20, 59, tzinfo=UTC)
    # A label that looks like a phone number is still only a label.
    assert phone_label.sender_label == "+90 555 000 00 00"
    assert result.participants()[0]["basis"] == "label shown in the export"


def test_android_month_first_12h_with_narrow_no_break_space() -> None:
    text = (
        f"12/31/21, 11:59{NNBSP}PM - Alice: Happy new year\n"
        f"1/1/22, 12:05{NNBSP}AM - Bob: Same to you\n"
    )
    result = parse_export(text, timezone="America/New_York")
    assert result.date_order.order == "month_first"
    assert result.clock == "12h"
    first, second = result.messages
    assert first.local_datetime == datetime(2021, 12, 31, 23, 59)  # noqa: DTZ001
    assert second.local_datetime == datetime(2022, 1, 1, 0, 5)  # noqa: DTZ001
    assert first.utc_datetime == datetime(2022, 1, 1, 4, 59, tzinfo=UTC)
    assert first.meridiem == "PM"


def test_ios_layout_system_notice_and_media_markers() -> None:
    text = (
        f"[31.12.21, 23:57:01] Proje Grubu: {LRM}Messages and calls are end-to-end encrypted.\n"
        f"[31.12.21, 23:58:02] Ayşe: {LRM}<attached: 00000003-PHOTO-2021-12-31-23-58-02.jpg>\n"
        f"[31.12.21, 23:58:30] Ayşe: {LRM}image omitted\n"
        "[31.12.21, 23:59:59] Mehmet Öztürk: Görüldü\n"
    )
    result = parse_export(text, timezone="unknown")
    notice, attached, omitted, plain = result.messages
    assert result.layout_counts == {"ios": 4}
    assert notice.kind == "system"
    assert notice.classification_basis == "ios_left_to_right_mark"
    assert attached.kind == "message"
    assert attached.attachments[0].reference == "00000003-PHOTO-2021-12-31-23-58-02.jpg"
    assert attached.attachments[0].marker == "attached"
    assert omitted.attachments[0].status == "omitted_by_export"
    assert omitted.attachments[0].reference is None
    assert plain.local_datetime == datetime(2021, 12, 31, 23, 59, 59)  # noqa: DTZ001
    # Unknown timezone: local time kept, no UTC instant claimed.
    assert plain.utc_datetime is None
    assert result.timezone == "unknown"


def test_ambiguous_dates_are_not_guessed() -> None:
    text = "03/04/21, 10:00 - Ali: bir\n05/06/21, 11:00 - Ali: iki\n"
    result = parse_export(text, timezone="Europe/Istanbul")
    assert result.date_order.order is None
    assert result.date_order.basis == "ambiguous"
    assert result.needs_input
    assert result.date_order.samples == ("03/04/21", "05/06/21")
    assert all(m.local_datetime is None and m.utc_datetime is None for m in result.messages)

    chosen = parse_export(text, date_order="day_first", timezone="Europe/Istanbul")
    assert chosen.date_order.basis == "explicit"
    assert chosen.messages[0].local_datetime == datetime(2021, 4, 3, 10, 0)  # noqa: DTZ001

    other = parse_export(text, date_order="month_first", timezone="Europe/Istanbul")
    assert other.messages[0].local_datetime == datetime(2021, 3, 4, 10, 0)  # noqa: DTZ001


def test_an_explicit_choice_the_export_contradicts_is_reported() -> None:
    text = "03/04/21, 10:00 - Ali: bir\n25/04/21, 11:00 - Ali: iki\n"
    result = parse_export(text, date_order="month_first", timezone="unknown")
    assert result.date_order.order == "month_first"
    assert result.date_order.proof_line == 2
    assert "cannot be read that way" in result.date_order.explanation
    assert result.invalid_timestamps == 1
    assert result.messages[1].time_note == "invalid_for_date_order"
    assert result.messages[1].local_datetime is None


def test_inconsistent_dates_need_a_decision() -> None:
    text = "25/04/21, 10:00 - Ali: bir\n04/25/21, 11:00 - Ali: iki\n"
    result = parse_export(text, timezone="unknown")
    assert result.date_order.order is None
    assert result.date_order.basis == "inconsistent"
    assert result.needs_input


def test_year_first_layout() -> None:
    result = parse_export("2021-12-31, 23:59 - Ali: iso\n", timezone="UTC")
    assert result.date_order.basis == "year_first_layout"
    assert result.messages[0].utc_datetime == datetime(2021, 12, 31, 23, 59, tzinfo=UTC)


def test_daylight_saving_gaps_and_overlaps_are_flagged_not_resolved() -> None:
    text = (
        "31/10/2021, 02:30 - Anna: overlap hour\n"
        "28/03/2021, 02:30 - Anna: skipped hour\n"
        "28/03/2021, 13:00 - Anna: ordinary\n"
    )
    result = parse_export(text, timezone="Europe/Berlin")
    overlap, gap, ordinary = result.messages
    assert overlap.utc_datetime is None
    assert overlap.time_note == "ambiguous_local_time"
    assert gap.utc_datetime is None
    assert gap.time_note == "nonexistent_local_time"
    assert ordinary.utc_datetime == datetime(2021, 3, 28, 11, 0, tzinfo=UTC)
    assert result.time_notes == {"ambiguous_local_time": 1, "nonexistent_local_time": 1}


def test_turkish_twelve_hour_markers() -> None:
    result = parse_export(
        "31.12.2021 11:59 ÖS - Deniz: akşam\n31.12.2021 09:15 ÖÖ - Deniz: sabah\n",
        timezone="unknown",
    )
    evening, morning = result.messages
    assert evening.local_datetime == datetime(2021, 12, 31, 23, 59)  # noqa: DTZ001
    assert morning.local_datetime == datetime(2021, 12, 31, 9, 15)  # noqa: DTZ001
    assert result.clock == "12h"


def test_android_attachment_markers_and_other_language_member_names() -> None:
    text = (
        "31/12/2021, 10:00 - Ali: IMG-20211231-WA0001.jpg (file attached)\n"
        "31/12/2021, 10:01 - Ali: <Media omitted>\n"
        "31/12/2021, 10:02 - Ali: DOC-20211231-WA0002.pdf (dosya ekli)\n"
        "31/12/2021, 10:03 - Ali: VID-20211231-WA0003.mp4 (file attached)\n"
    )
    result = parse_export(
        text, timezone="unknown", member_names=frozenset({"DOC-20211231-WA0002.pdf"})
    )
    attached, omitted, other_language, not_in_archive = result.messages
    assert attached.attachments[0].reference == "IMG-20211231-WA0001.jpg"
    assert omitted.attachments[0].status == "omitted_by_export"
    # An unrecognized marker is still a reference when the export contains that exact file.
    assert other_language.attachments[0].reference == "DOC-20211231-WA0002.pdf"
    assert other_language.attachments[0].marker == "member_name"
    # A referenced file that is not in the archive stays referenced; the importer marks it missing.
    assert not_in_archive.attachments[0].reference == "VID-20211231-WA0003.mp4"


def test_preamble_edited_and_deleted_markers_and_windows_line_endings() -> None:
    text = (
        "\ufeffExported chat header line\r\n"
        "31/12/2021, 10:00 - Ali: first <This message was edited>\r\n"
        "31/12/2021, 10:01 - Ali: This message was deleted\r\n"
    )
    result = parse_export(text, timezone="unknown")
    edited, deleted = result.messages
    assert result.preamble_lines == 1
    assert edited.edited_marker
    assert edited.text == "first"
    assert deleted.deleted_marker
    assert text[deleted.char_start : deleted.char_end] == (
        "31/12/2021, 10:01 - Ali: This message was deleted"
    )


def test_notices_quoting_a_colon_are_not_mistaken_for_labels() -> None:
    text = "31/12/2021, 10:00 - Ali changed the subject to \u201cPlan: B\u201d\n"
    result = parse_export(text, timezone="unknown")
    assert result.messages[0].kind == "system"


def test_message_limit_truncates_and_reports_where() -> None:
    lines = "".join(f"31/12/2021, 10:{i:02d} - Ali: {i}\n" for i in range(10))
    result = parse_export(lines, timezone="unknown", max_messages=3)
    assert len(result.messages) == 3
    assert result.truncated_at_line == 4


def test_text_that_is_not_an_export_is_refused() -> None:
    with pytest.raises(WhatsAppParseError) as caught:
        parse_export("just some notes\nwithout timestamps\n", timezone="unknown")
    assert caught.value.code == "no_messages"


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", "Europe/Istanbul; rm"])
def test_unknown_or_malformed_timezones_are_refused(zone: str) -> None:
    with pytest.raises(WhatsAppParseError) as caught:
        parse_export("31/12/2021, 10:00 - Ali: x\n", timezone=zone)
    assert caught.value.code == "invalid_timezone"


def test_line_separator_characters_stay_inside_a_message() -> None:
    text = "12/03/2024, 09:15 - Ayşe: satır bir\u2028satır iki\n13/03/2024, 10:00 - Can: tamam\n"
    result = parse_export(text, date_order="day_first")
    assert [m.line_start for m in result.messages] == [1, 2]
    first = result.messages[0]
    assert text[first.char_start : first.char_end].endswith("satır iki")
