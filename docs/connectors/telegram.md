# Telegram public channel (`telegram.public_channel` 1.0.0)

Collects one explicitly selected **public** Telegram channel. It never joins chats, sends
messages, reads private groups or expands beyond the selected source. Design:
[ADR 0009](../adr/0009-social-connector-capabilities.md).

| | |
| --- | --- |
| Input | public username (5-32 letters, digits, underscores, starting with a letter); `@name`, `t.me/name` and `https://t.me/s/name` are accepted |
| Parameters | `capability` (`public_web_preview` default, or `bot_api_chat_info`) |
| Credentials | `bot_token` for the Bot API capability only |
| Endpoints | `TRACEHOLLOW_TELEGRAM_WEB_BASE_URL` (default `https://t.me`), `TRACEHOLLOW_TELEGRAM_BOT_API_BASE_URL` (default `https://api.telegram.org`) |
| Limits | up to 10 pages, 120 s per run, 2 concurrent runs, 2 s between preview requests |
| Verification | **fixture-tested**; not live-verified |

## Capabilities

| Capability | Status | Access | Collection mode |
| --- | --- | --- | --- |
| Unofficial: public channel web preview | implemented | Unofficial public web page (`t.me/s/<channel>`) | Platform probe |
| Official Bot API: public chat metadata | implemented | Official API (Bot API 10.3) | Third-party lookup |
| User-account session (MTProto client, e.g. Telethon) | not implemented | Unofficial client | — |
| Private groups, joining and messaging | **excluded** | — | — |

MTProto sessions are not implemented because client libraries open raw MTProto TCP connections
that the connector network policy does not govern, and a user session can reach far more than the
selected source. Telethon's GitHub repository was archived and moved to Codeberg in 2026-02 and is
in maintenance mode.

### Public channel web preview

`GET {web}/s/{channel}`; older posts with `?before={oldest post number}` from the page's
"load more" element. The layout is undocumented (checked against the observed markup and
open-source parsers on 2026-09-16).

- **Returns:** channel title, description and displayed counters; per post: post ID
  (`channel/number`), text (scripts and styles ignored, quoted reply text excluded), `datetime`,
  edited mark, displayed view count, links, forwarded-from name and link, media types present,
  permalink.
- **Never returns:** numeric channel ID, exact counts, media files, reactions in most layouts,
  posts hidden from the preview, private channels or groups, members.
- **Stored:** the HTML snapshot (not indexed) and a derived JSON list of parsed posts (indexed),
  `telegram_channel_preview` and `telegram_post` observations. No entity is created because the
  preview gives no stable ID.

| Situation | Outcome and code |
| --- | --- |
| Channel info or posts found | `findings`; coverage lists newest and oldest post numbers |
| Post numbers missing between visible posts | still `findings`; `post_numbers_not_visible` and a note: deleted, service messages or hidden — the preview does not say which |
| Redirect away from `/s/` or HTTP 404 | `access_denied` / `telegram_preview_unavailable` — not evidence that the source does not exist |
| HTTP 429 | `rate_limited` / `telegram_throttled` |
| No channel or post elements | `parse_error` / `unexpected_page_layout` |
| A later page fails | `partial` |

### Bot API chat metadata

`GET {bot}/bot{token}/getChat?chat_id=@{channel}` and `getChatMemberCount?chat_id={id}`
(core.telegram.org/bots/api, Bot API 10.3, checked 2026-09-16). Bots cannot read the history of
channels they are not members of; this capability does not try.

- **Returns:** `id`, `type`, `title`, `username`, `active_usernames`, `description`,
  `linked_chat_id`, `has_protected_content`, `has_visible_history`, `invite_link`, pinned message
  ID, member count.
- **Stored:** both JSON responses (`credentialed`) with the token replaced by `[redacted]` in the
  recorded URLs and any error text; a `platform_account` entity matched by the numeric chat ID.

| Situation | Outcome and code |
| --- | --- |
| Chat returned | `findings` (member count failure makes the page incomplete) |
| Token not configured or malformed | `authentication_required` / `credential_not_configured` or `credential_invalid` |
| `401 Unauthorized` | `authentication_required` / `telegram_bot_token_rejected`; credential marked rejected |
| `400 chat not found` | `no_findings` with a note: may not exist, be a user or private chat, or be renamed |
| `429` | `rate_limited` / `telegram_flood_limit` using `parameters.retry_after` |
| `403` | `access_denied` / `telegram_forbidden` |
| 5xx | `unavailable` |

## Tests

`tests/test_social_connectors.py` (posts, edits, links, forwarded posts, reply exclusion, script
content, gaps, pagination, private channel, layout change, throttling, Bot API success, token
redaction, all Bot API errors), `tests/test_social_collection.py` (provenance through the engine),
`scripts/verify-phase4.sh`.
