# Importing WhatsApp chat exports

Import only chats you are authorized to review (for example, provided by a participant with
consent or under a legal process). Tracehollow imports **exports**; it does not discover private
messages, access accounts or decrypt anything. Design: [ADR 0008](../adr/0008-authorized-imports-and-document-processing.md).

## Exporting

WhatsApp's own *Export chat* feature produces:

- **Android:** `WhatsApp Chat with <name>.txt`, or a `.zip` with that file and the attached media
  ("Include media").
- **iOS:** a `.zip` containing `_chat.txt` and the attached media, or the text alone.

Import the `.txt` or the `.zip` unchanged. Do not re-save the text in another editor; the
original bytes are the evidence and line numbers refer to them.

## In the workspace

**Evidence → Import a WhatsApp chat export.** Fill in:

| Field | Meaning |
| --- | --- |
| Export file | `.txt` or `.zip`, up to 128 MiB (`TRACEHOLLOW_IMPORT_MAX_ARCHIVE_BYTES`); chat text up to 32 MiB |
| Timezone of the exporting phone | IANA name (for example `Europe/Istanbul`), or **Unknown** |
| Date order | **Detect** (default), day first, month first or year first |
| Import origin | who provided it, how it was obtained and your authorization (required) |

API: `POST /api/v1/cases/{case}/imports/whatsapp` (multipart: `file`, `import_origin`, `timezone`,
`date_order`, optional `title`, `source_reference`, `description`).

The original is stored first; the worker then parses it. Progress, gaps and limitations appear
under **Processing jobs**.

## Dates, times and timezones

Exports write dates without saying their order (`03/04/2024` can be 3 April or 4 March) and
times without a timezone.

- **Detect** uses proof only: a first number above 12 means day first, a second number above 12
  means month first, a four-digit year first means year first. If there is no proof, or the proof
  contradicts itself, the job stops in **Needs your input**, shows sample dates as written and
  derives nothing until you choose. An explicit choice that contradicts a date in the export is
  recorded with that line.
- **Timezone given:** each message gets a UTC instant and appears on the UTC timeline. Times that
  fall into a daylight-saving gap or overlap are marked and left without a UTC instant.
- **Timezone unknown:** messages keep their local wall-clock time and appear in the timeline's
  *Local time only* section, never mixed with UTC.
- Every message keeps its timestamp exactly as written (`timestamp_text`). 12-hour clocks
  (`AM`/`PM`, Turkish `ÖÖ`/`ÖS`) and narrow no-break spaces are supported.

## What is recorded

- **Chat text:** for a `.zip`, the chat text file becomes its own evidence record (byte-exact,
  derived from the archive) and is indexed for AI retrieval under the case's AI policy. For a `.txt`
  import, the original is the chat text.
- **Messages and system events:** one observation each (`whatsapp_message`,
  `whatsapp_system_event`) citing the line range and character offsets in the chat text, with the
  sender label, text (multiline, Unicode and Turkish characters preserved), local time, timezone,
  date-order basis, edit and deletion markers and attachment references.
- **Sender labels** are the names or numbers the exporting phone showed. They are **not** verified
  identities; no entity, account or phone-number identifier is created from them.
- **Attachments** in a `.zip` are stored as inert evidence files (media type from their bytes, never
  rendered or executed) and linked from the message that references them. References resolve to
  `present`, `missing` (named but not in the import) or `omitted_by_export` ("<Media omitted>").
  Missing attachments make the job *partial* but do not fail it.

## Safety limits

Archives are screened before anything is read: at most 2,000 entries, 128 MiB per entry, 512 MiB in
total, and an expansion ratio of 200. Entries with absolute or `..` paths, drive letters, control
characters, symbolic links, encryption or duplicate names are skipped and listed in the job result.
Nothing is extracted to the filesystem. At most 200,000 messages are parsed per export. All limits
are `TRACEHOLLOW_IMPORT_*` and `TRACEHOLLOW_WHATSAPP_MAX_MESSAGES` settings.

## Reprocessing and deletion

**Process again** (or `POST /api/v1/cases/{case}/evidence/{original}/processing`) replaces the
records the previous run derived. Deleting the original deletes its chat text, attachments,
observations and files. Cancelling a waiting or queued job stops it immediately.

## Supported layouts and known limits

- Android: `DD/MM/YYYY, HH:MM - Name: text` and variants (`.`/`-` separators, two-digit years,
  12-hour clocks).
- iOS: `[DD.MM.YY, HH:MM:SS] Name: text`, including the left-to-right mark before system messages.
- Group events (created, added, left, changed subject), "This message was deleted", "<This message
  was edited>" and attachment markers in English exports. Localized system-message wording is kept
  as text but may be classified as a message.
- Media content is stored, not analysed.

Tests: `tests/test_whatsapp_parser.py`, `tests/test_import_archives.py`,
`tests/test_whatsapp_import.py`, `scripts/verify-phase4.sh`.
