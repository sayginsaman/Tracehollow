# Product context: Tracehollow

Strategic design context for interface work. The product scope, phases and acceptance criteria live
in [PRD.md](PRD.md); implementation status lives in [docs/STATUS.md](docs/STATUS.md). This file
does not add requirements. It records who the interface serves and what it must feel like.

## Register

product

Tracehollow is an authenticated, local-first application: every screen is a task surface (cases,
evidence, collection runs, imports, analysis, reports, configuration). There is no marketing
surface. Design serves the investigation.

## Users

From PRD §2, the people who sit in front of this interface are:

- **Security researchers** mapping public infrastructure and online assets.
- **Threat intelligence analysts** correlating public reports with domains, URLs, IPs,
  organizations and accounts.
- **Investigative researchers** organizing public documents, chat exports they are authorized to
  review, and traceable findings.
- **Individuals** reviewing their own public digital footprint.

Their context: a single analyst (or a small team on one machine) at a desk, for long sessions,
reading source text, chat lines, PDF extracts and collection outcomes, and cross-checking every
claim against its evidence. Much of the material is Turkish or mixed-language. They switch
between broad orientation (what happened in this case, what needs me) and close reading (this
passage, this hash, this line of a chat export). Many are careful by profession: they notice
overstated certainty and distrust interfaces that look more confident than the data.

## Product purpose

Help an analyst run a lawful, reproducible investigation on their own machine: define a case and
its scope, collect public or authorized material with honest coverage, preserve evidence with
provenance, follow relationships, compare observations over time, ask questions whose answers cite
case evidence, and export a report that keeps its uncertainty. Success looks like an analyst who can
always answer "where did this come from, when, how, and how sure are we?" within one or two
clicks.

## Brand personality

**Composed, precise, candid.**

- **Composed:** quiet surfaces, predictable structure, no urgency theatre. Long sessions should not
  tire the eye.
- **Precise:** exact times with their basis (UTC, local with unknown timezone, collection only),
  exact citations (page, line, character range), identifiers and hashes shown verbatim.
- **Candid:** unknown, partial, blocked, synthetic, fixture-tested and AI-generated states are
  named plainly and never styled as success. The interface never implies more than the backend
  established.

Voice: plain English sentences, short labels, no jargon where a plain word exists, no hype, no
em dashes in interface copy.

## Anti-references

What Tracehollow must not look or feel like:

- **Hacker/cyberpunk aesthetics:** dark terminals, neon green or cyan on black, glitch effects,
  scanlines, matrix rain, "threat map" globes.
- **Decorative dashboards:** hero metrics, big-number KPI cards, fabricated scores or confidence
  percentages, sparkline wallpaper, gradient accents.
- **Glassmorphism and glow:** frosted panels, blurred backdrops, drop-shadow halos.
- **Consumer chat apps:** speech bubbles and avatars for AI answers that hide citations and
  uncertainty behind a friendly persona.
- **Surveillance products:** anything that suggests identity resolution, people search or
  private-account access the product does not and must not perform. A matching username or a
  WhatsApp sender label is never presented as an established identity.
- **Giant headers and excessive motion:** oversized titles, orchestrated page-load animation,
  bouncing or elastic transitions.

## Design principles

1. **Evidence first, interpretation labelled.** Collected public material, authorized imports,
   extracted text, OCR text, analyst assertions and AI-generated content always carry distinct,
   consistent labels. Provenance is one click away from any claim.
2. **Unknown is a valid state.** Partial, failed, cancelled, rate-limited, access-required and
   needs-input outcomes each get their own plain-language state, never a green check.
3. **Familiar over novel.** Standard sidebar navigation, breadcrumbs, tables, tabs and forms.
   An analyst fluent in mainstream work tools should trust every control on sight.
4. **Case context is always visible.** Inside a case the analyst always sees which case they are
   in, its status, and where they are within it.
5. **Progressive disclosure, not hidden truth.** Show the decision-relevant facts first; put
   technical metadata (hashes, parameter snapshots, raw payloads) one step away. Caveats that
   change interpretation are never hidden.
6. **Readable for hours.** Comfortable measure for prose, tabular figures for data, restrained
   color, quiet chrome, contained scrolling for wide content.
7. **Honest data only.** Every number on screen comes from a real record or query. No invented
   metrics, placeholder activity or decorative charts.

## Accessibility and inclusion

- WCAG 2.2 AA as the floor: text contrast at least 4.5:1 (3:1 for large text and UI boundaries),
  visible focus on every interactive element, target size at least 24px.
- Status never by color alone: every state pairs color with an icon or glyph and a text label.
- Full keyboard operation, including graph inspection through the equivalent edge table.
- Screen-reader structure: one `h1` per page, landmark regions, labelled forms, live regions only
  for genuinely dynamic updates.
- Layout holds at 390px width and at 200% zoom with no page-level horizontal scrolling; wide
  tables and the graph scroll inside their own containers.
- `prefers-reduced-motion` removes non-essential transitions; `prefers-color-scheme` selects the
  light or dark theme, which the analyst can override.
- Unicode-safe rendering: Turkish characters (ı, İ, ş, ğ, ç, ö, ü) in names, identifiers and
  evidence render correctly in both typefaces; long URLs, hashes and filenames wrap or truncate
  with a way to see and copy the full value.
- Interface language is English, written in plain sentences that localize cleanly.
