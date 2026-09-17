# YouTube (Data API) (`youtube.data_api` 1.0.0)

Public channel, upload, video and comment metadata through the official YouTube Data API v3.
YouTube sees the request; channel owners are not contacted. Design:
[ADR 0009](../adr/0009-social-connector-capabilities.md).

| | |
| --- | --- |
| Inputs | `username` (channel handle, with or without `@`), `youtube_channel_id` (`UC…`), `youtube_video_id` (11 characters) |
| Parameters | `capability` (`channel_uploads` default for handles and channel IDs, `video_comments` for video IDs) |
| Credentials | `api_key` (Google Cloud API key with the YouTube Data API v3 enabled), sent only in the `X-Goog-Api-Key` header |
| Endpoint | `TRACEHOLLOW_YOUTUBE_API_BASE_URL` (default `https://www.googleapis.com/youtube/v3`) |
| Limits | up to 10 pages, 50 uploads or 100 comment threads per page, 120 s per run, 2 concurrent runs |
| Quota | 1 unit per request; default 10,000 units per day per project, reset at midnight Pacific Time; recorded per page |
| Verification | **fixture-tested**; not live-verified |

API details (quota costs, `channels.list` filters, `commentThreads.list`, core errors) were checked
on developers.google.com on 2026-09-16.

## Capabilities

| Capability | Status | Access |
| --- | --- | --- |
| Official API: channel and uploaded videos | implemented | `channels.list` (`forHandle` or `id`), then the uploads playlist via `playlistItems.list` |
| Official API: video and public comments | implemented | `videos.list`, then `commentThreads.list` (`order=time`, `textFormat=plainText`) |
| Official API: caption tracks (transcripts) | not implemented | `captions.download` needs OAuth by someone who can edit the video (and 200 units) |
| Unofficial transcript endpoints | not implemented | no supported, authorized source |

**Transcripts are not collected and never promised.**

## What is stored

- Each JSON response (`credentialed`); the API key is not part of any recorded URL.
- Channel uploads: a `platform_account` entity matched by channel ID, a `youtube_channel`
  observation (title, description, custom URL, country, published date, counts, hidden subscriber
  flag, uploads playlist), `youtube_video` observations per upload with privacy status and a
  `placeholder` flag for "Private video" / "Deleted video" entries.
- Video comments: a `youtube_video` observation, then `youtube_comment` observations per thread
  (IDs, author display name and channel ID with a note that names are labels, text, published and
  updated times, `edited`, like and reply counts). Replies are not fetched.

## Outcomes

| Situation | Outcome and code |
| --- | --- |
| Channel or video returned | `findings` |
| No channel or video for the handle or ID | `no_findings` with a note (renamed, terminated, private or removed) |
| `commentsDisabled` | `findings` with coverage `comments_status: disabled` and a note — not the same as no comments |
| Video disappears before comments are read (`videoNotFound`) | `unavailable`; earlier pages kept (`partial`) |
| API key not configured | `authentication_required` / `credential_not_configured` |
| `keyInvalid`, "API key not valid", 401 | `authentication_required` / `youtube_key_rejected`; credential marked rejected |
| `quotaExceeded`, `dailyLimitExceeded` | `rate_limited` / `youtube_quota_exceeded`, waiting until midnight Pacific Time (longer than the allowed wait, so the run stops with retry information) |
| `rateLimitExceeded`, `userRateLimitExceeded`, 429 | `rate_limited` / `youtube_rate_limited` |
| `accessNotConfigured`, `forbidden`, `ipRefererBlocked`, other 403 | `access_denied` |
| `processingFailure`, 5xx | `unavailable` |
| Input type not accepted by the capability | validation error when saving the query |

## Tests

`tests/test_social_connectors.py` (uploads with placeholders, comments with edits, disabled
comments, unavailable video, quota and key errors, quota reset time, input validation),
`tests/test_social_collection.py` (stored key used in a header and absent from evidence),
`scripts/verify-phase4.sh`.
