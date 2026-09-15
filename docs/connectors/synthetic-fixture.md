# Synthetic fixture (`synthetic.fixture` 1.0.0)

Deterministic generated data for development, demonstrations and tests. It contacts no network
service; every value is derived from the query input, hostnames use the reserved `.example` domain
and every record is labelled synthetic.

| | |
| --- | --- |
| Mode | Synthetic fixture |
| Inputs | username, domain, email |
| Parameter | `scenario`: `findings`, `no_findings`, `partial`, `failure`, `flaky`, `rate_limited`, `authentication_required`, `access_denied`, `parse_error`, `slow` |
| Output | JSON page evidence, `candidate_account` observations, synthetic `platform_account` and `domain` entities with observed `links_to` relationships |
| Runs on | the internal `worker` (no network access) |

It exists to exercise every outcome, retries, partial pages, cancellation and recovery without a
real source. Its results say nothing about any real account, domain or person.
