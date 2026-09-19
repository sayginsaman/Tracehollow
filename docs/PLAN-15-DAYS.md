# Fifteen days to something sellable

The candidate is complete as software and unproven as a product. Two gaps decide whether anyone
pays: **no connector that touches a social platform has ever run against the live platform**, and
**nobody outside this repository has tried to use it**. Fifteen working days is enough to close the
first gap and open a conversation about the second. It is not enough to build an enterprise tier,
so this plan does not pretend otherwise.

Everything below is either done by the owner or by an assistant working in the repository. Dates
are working days from the start, not calendar days.

## What this plan assumes

- The demonstration dataset and the documentation are in place (they are).
- The repository is public under MIT and the owner accepts that the core stays MIT.
- No budget is committed beyond free API tiers and the owner's time.

## Days 1-3: make the claims checkable

Nothing else matters until a stranger can verify what the README says.

| Task | Done when |
| --- | --- |
| Get continuous integration green on a hosted runner | A run on `main` passes all three jobs, and the README carries the real Actions badge rather than a static one |
| Live-verify **Telegram** public channel preview | `scripts/live-smoke.sh` records a passing check against one public, institutional channel; `verification_status` becomes `live_verified` and the dated record is committed |
| Live-verify **YouTube** Data API | Same, with a Google Cloud API key restricted to the YouTube Data API v3, against one public channel and one public video |
| Start **Meta App Review** for Instagram Business Discovery | Submission filed. It takes four to six weeks, so it has to start on day one or it will not land inside any plan |

Telegram and YouTube are first because neither needs a paid account and both are genuinely useful
to the buyers named below. After this, three of the nine connectors are live-verified on public
targets rather than one.

## Days 4-6: the gap a buyer hits first

Two things are missing that every demo will expose.

- **A report a lawyer can file.** The HTML report is honest and self-contained, but the people who
  pay for defensible findings send PDFs. Add a PDF export that keeps the citation links working as
  page references, keeps the label legend, and records the same manifest.
- **Case collaboration that does not need a terminal.** Roles and membership exist at the API and
  in the interface; what is missing is inviting somebody. A minimal invitation flow (administrator
  creates the account, hands over a one-time link) is enough.

Neither is research. Both are a day of work plus tests.

## Days 7-9: put it in front of five people

No new features this window. Five conversations, thirty minutes each, with people who investigate
for a living:

- a due diligence or KYC analyst at a law firm or consultancy,
- an insurance fraud investigator,
- a corporate security or trust and safety analyst,
- an investigative journalist,
- one person who already pays for Maltego, Skopenow or Hunchly.

Show the demonstration case, then ask three questions and write the answers down verbatim:

1. Which source would you need that we do not have?
2. What would stop you using this tomorrow?
3. What do you pay today, and for what?

The answers decide the next two windows. Guessing here is what kills tools like this.

## Days 10-12: build the one thing they all named

Expect it to be a source, and expect it to be unglamorous: company registries, sanctions and
politically-exposed-person lists, court records, or breach-exposure lookups. Those are what due
diligence actually runs on, and none of them is implemented.

Build exactly one, end to end, following `docs/development/connectors.md`: descriptor with honest
coverage, contract tests, its own documentation page, and a live check on a public target. One
finished source beats three half-finished ones.

If instead all five named a missing feature rather than a source, build that.

## Days 13-15: package and price

| Task | Done when |
| --- | --- |
| Tag **0.1.0** | Release notes published from the draft, with the limitations section intact |
| Open the paid surface | A one-page offer: installation and a custom connector for a fixed fee. Services are the only thing sellable on day fifteen, and they teach which connector people pay for |
| Decide the commercial boundary | Written down: the core stays MIT; a separate repository holds what only organisations want — single sign-on, multi-tenancy, sealed reports, audit export, MISP and OpenCTI bridges |
| Publish one piece of evidence | A short write-up of the walkthrough, aimed at the segment that answered best in days 7-9 |

## What this plan deliberately refuses

- **Chasing volume.** Competitors sell "more data". This tool's advantage is that every finding
  carries its origin, its hash and its collection outcome, that absence is never reported as
  evidence, and that nothing leaves the machine. Positioning it on breadth loses to Maltego;
  positioning it on defensibility has no direct competitor.
- **Scraping what the terms forbid.** The Instagram capability model exists for a reason. A buyer
  in a regulated industry cannot deploy a tool that breaks a platform's terms, and the first demo
  where a login wall is reported honestly is what wins that buyer.
- **Building an enterprise tier before anyone asks.** Single sign-on and multi-tenancy are days of
  work each and zero of them are validated.
- **Selling to law enforcement this quarter.** The largest budgets, the longest cycle, and ethical
  and export questions that deserve a deliberate decision rather than an opportunistic one.

## The honest risk

The hardest part of this business is not the software; it is source access. Every valuable source
is either paid, rate-limited, or restricted by terms. A tool that respects those limits looks
weaker in a feature comparison and is the only kind a regulated buyer can actually deploy. That
trade is the product. If the five conversations in days 7-9 say otherwise, the trade is wrong and
the plan should change, not the principle.
