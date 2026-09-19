# Preparing the Meta App Review for Instagram Business Discovery

Tracehollow's Instagram connector is implemented and fixture-tested, and it has never spoken to
Meta's API, because Business Discovery needs permissions that only App Review grants. This page
collects what the application needs, drafts the text the review form asks for, and is honest about
where the application is likely to struggle.

Read [instagram.md](instagram.md) first: it states exactly what the connector requests, what comes
back, and what is deliberately excluded.

## What is actually being asked for

| | |
| --- | --- |
| Endpoint | `GET /v25.0/{ig_user_id}?fields=business_discovery.username({username}){…}` |
| Permissions | `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement` |
| App type | Business |
| Returns | The looked-up professional account's `id`, `username`, `biography`, `website`, `followers_count`, `media_count`, and per media item the caption, type, permalink, timestamp, like and comment counts |
| Never returns | Personal or age-gated accounts, private content, stories, follower and following lists, messages, comment text, profile name and picture |

Business Discovery reads **other people's public professional accounts** through an account you
administer. That is the part a reviewer will focus on, so the application has to be precise about
why an investigation tool needs it and what happens to the data afterwards.

## Before the form can be filled in

1. **A Meta app of type Business**, created at developers.facebook.com, with the Instagram Graph API
   product added.
2. **A Facebook Page and a professional (Business or Creator) Instagram account** that the Page is
   connected to, and that the applicant administers. The account's Instagram user ID is the
   `ig_user_id` credential Tracehollow stores.
3. **Business verification** of the legal entity behind the app: Meta asks for a registered business
   name, address, and a document or a verifiable web presence. A personal project without a
   registered entity is where most applications stop.
4. **Three public URLs** the form requires, which this project does not have yet:
   - a privacy policy,
   - terms of service,
   - a data deletion instructions URL (a page describing how a person asks for deletion; the
     callback form is only needed for Facebook Login).
5. **App icon (1024×1024), display name, category and a short description.**
6. **A screencast** that shows each requested permission actually being used, end to end.

Items 3 and 4 are work outside the repository and should start before anything else, because they
gate the submission rather than follow it.

## Draft text for the review form

Meta asks, per permission, what the app does with it and how a person benefits. Keep it factual;
reviewers reject vague or aspirational descriptions. These drafts describe what the code does.

### `instagram_basic`

> Tracehollow is a self-hosted investigation workspace used by analysts who must be able to show
> where each finding came from. The operator connects their own professional Instagram account and
> uses Business Discovery to look up a professional account that is part of an authorized enquiry,
> for example a company account being checked in a due-diligence review. `instagram_basic` is what
> lets the app read that account's public profile fields: username, biography, website, follower
> count and media count. The app stores the API response as evidence with its SHA-256 hash and the
> time it was collected, so the analyst can later show exactly what the account looked like on that
> date. Nothing is written back to Instagram, and the app never requests private content, stories,
> follower lists or messages.

### `instagram_manage_insights`

> Business Discovery returns the looked-up professional account's public media through the same
> call, and Meta gates that field behind `instagram_manage_insights`. Tracehollow uses it only to
> read the public media list of the account being looked up: caption, media type, permalink,
> timestamp and the like and comment counts. It does not read insights or analytics for the
> operator's own account, and it does not request metrics that are not part of the Business
> Discovery response.

### `pages_read_engagement`

> The professional Instagram account that performs the lookup is connected to a Facebook Page the
> operator administers. `pages_read_engagement` is required for the app to resolve that Page and its
> connected Instagram user ID, which is the account the Business Discovery request is made from.
> The app reads nothing else from the Page and posts nothing to it.

### How the data is used, stored and deleted

> Tracehollow is self-hosted: each operator runs it on their own machine or server with Docker
> Compose, and the developer of the software never receives their data. Responses are stored in the
> operator's own PostgreSQL database, labelled with the account they came from, when they were
> collected and a SHA-256 hash of the exact bytes. An operator can delete an individual record, a
> whole case, or the entire installation with its volumes, and the software ships a retention
> feature that removes records on a schedule. Data is never sold, never shared with third parties
> and never used to train models. An optional local language model can read stored evidence to
> answer an analyst's question, and that model runs on the operator's own machine.

## The screencast

Meta requires a recording that shows each permission in use. Plan it as one continuous take:

1. The operator opens Tracehollow's **Sources** page and sees the Instagram connector listed with
   its capabilities and the credentials it needs.
2. The operator stores the access token and Instagram user ID (the fields are write-only; the token
   is never displayed again).
3. The operator creates a saved query with the `official_business_discovery` capability and a
   professional account's username, and runs it.
4. The run reports its outcome, and the evidence record shows the stored response with its
   provenance panel: acquisition method, collection time and SHA-256.
5. The operator opens the entity created from the response and shows that only the documented public
   fields are present.

Use a professional account you control as the lookup target, so nothing in the recording involves a
third party who has not agreed to appear in it.

## Where this application is likely to struggle

Say these out loud when deciding whether to file, rather than discovering them at rejection:

- **Business Discovery is designed for social-media management and analytics tools.** An
  investigation workspace is an unusual fit, and a reviewer may read "OSINT" as monitoring people.
  The application should lean on what the software actually restricts: professional accounts only,
  public fields only, no private content, no follower lists, no messages, explicit provenance and a
  retention feature.
- **Self-hosted software is awkward to review.** The reviewer cannot log into a hosted product. The
  screencast has to carry the whole demonstration, and it must be complete enough that nothing is
  left to imagination.
- **Business verification needs a registered entity.** Without one the application cannot proceed,
  regardless of how good the use case is.
- **Approval is not guaranteed and rejection is not a defect in the connector.** If the application
  is refused, the honest outcome is that `official_business_discovery` stays fixture-tested and the
  capability matrix keeps saying so. The connector already handles that state: without credentials
  a run ends as `authentication_required` with `credential_not_configured`, and nothing is invented.

## What happens after approval

1. Store the token and Instagram user ID in Tracehollow as an administrator (**Sources → Instagram →
   credentials**); both are write-only and encrypted at rest.
2. Add a live check definition for `instagram.account` in `scripts/live_smoke.py`, following the
   YouTube ones: a professional account you control as the target, one page, the credential named in
   the authorization file.
3. Run it through `scripts/live-smoke.sh` with a dated authorization, record the result in
   [live-smoke.md](live-smoke.md), and only then change `verification_status` to `live_verified`.
4. Update the capability matrix, the README row and `docs/STATUS.md`.

Long-lived tokens expire. Meta's user access tokens last 60 days unless exchanged; plan for the
credential to be replaced, and expect `authentication_required` with `instagram_token_invalid` when
it lapses, which is what the connector already reports.
