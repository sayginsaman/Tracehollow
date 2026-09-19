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
| Verification | channel uploads and video comments **live verified 2026-09-19** against YouTube's own channel and the first video published on the platform (see [live-smoke.md](live-smoke.md)); captions and transcript capabilities are not implemented |

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

## Live verification log

| Date | Version | Target category | Outcome | Reviewer |
| --- | --- | --- | --- | --- |
| 2026-09-19 | 1.0.0 | YouTube's own channel `UCBR8-60-B28hp2BmDPdntcQ`, two pages (channel plus one uploads page), capability `channel_uploads` | `partial` as expected at a two-page limit: the channel with its id and title, 5 uploads each carrying a video id, Data API quota recorded; responses stored as `third_party_api` / `credentialed` evidence ([record](live-smoke/2026-09-19-results-youtube.json)) | implementing assistant; authorized by the repository owner ([authorization](live-smoke/2026-09-19-authorization-youtube.json)) |
| 2026-09-19 | 1.0.0 | `jNQXAC9IVRw`, the first video published on YouTube, two pages, capability `video_comments` | `partial` as expected: 5 top-level comment threads, each tied to the requested video id ([same record](live-smoke/2026-09-19-results-youtube.json)) | same |

Scope: two capabilities, two pages each, with an API key restricted to the YouTube Data API v3 in a
Google Cloud project created for this purpose. Not covered by the live check: deeper pagination,
quota exhaustion, disabled comments, unavailable or age-restricted videos, channel handles as input
(the checks used a channel ID), and every error class in the table above, which remain covered by
fixtures.

A first attempt the same day bounded each check to one page and returned no items, because the
connector spends its first page on the channel or video itself. That was recorded as a failed live
check and the bound was corrected before re-running; the connector was not relabelled until the
corrected run passed.
