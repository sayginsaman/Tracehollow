---
name: Tracehollow
description: A composed, evidence-first investigation workspace for long reading sessions.
colors:
  canvas: "oklch(0.975 0.004 85)"
  surface: "oklch(0.995 0.002 85)"
  sunken: "oklch(0.962 0.005 85)"
  sidebar: "oklch(0.955 0.006 85)"
  line: "oklch(0.9 0.007 85)"
  line-strong: "oklch(0.65 0.01 85)"
  ink: "oklch(0.235 0.012 80)"
  muted: "oklch(0.47 0.013 80)"
  subtle: "oklch(0.6 0.011 80)"
  accent: "oklch(0.48 0.125 262)"
  accent-strong: "oklch(0.41 0.115 262)"
  accent-soft: "oklch(0.95 0.022 262)"
  on-accent: "oklch(0.985 0.004 262)"
  ok: "oklch(0.48 0.1 150)"
  ok-soft: "oklch(0.96 0.028 150)"
  ok-line: "oklch(0.83 0.06 150)"
  warn: "oklch(0.5 0.105 62)"
  warn-soft: "oklch(0.965 0.035 85)"
  warn-line: "oklch(0.84 0.08 80)"
  bad: "oklch(0.5 0.165 27)"
  bad-soft: "oklch(0.962 0.02 27)"
  bad-line: "oklch(0.84 0.065 27)"
  ai: "oklch(0.49 0.13 300)"
  ai-soft: "oklch(0.962 0.022 300)"
  ai-line: "oklch(0.84 0.06 300)"
typography:
  title:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: "1.75rem"
    letterSpacing: "-0.01em"
  heading:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: "1.5rem"
  subheading:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: "1.25rem"
  body:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
  read:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: "1.5rem"
  label:
    fontFamily: "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: "1rem"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: "1.25rem"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
  "10": "40px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
    textColor: "{colors.on-accent}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-secondary-hover:
    backgroundColor: "{colors.sunken}"
  button-danger:
    backgroundColor: "{colors.bad}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 10px"
    height: "36px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "16px"
  nav-item:
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "0 10px"
    height: "32px"
  nav-item-active:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-strong}"
  status-label:
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
  table-header:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
---

# Design System: Tracehollow

## 1. Overview

**Creative North Star: "The Case Ledger"**

Tracehollow reads like a well-kept case ledger on a clean desk: archive-paper surfaces, ink for
action, and every entry traceable to its source. The interface is quiet so the material can speak.
Structure does the work that decoration does elsewhere: a persistent sidebar, a compact header
with breadcrumbs, one heading per page, panels separated by hairlines, tables with tabular
figures. Color is reserved for meaning: the ink-blue accent marks actions and the current place;
the semantic colors mark outcomes; violet marks AI-generated content and nothing else.

Density is deliberate. Lists and tables are compact enough to scan a case at a glance, while
reading surfaces (evidence previews, chat lines, AI answers) open up to a 15px reading size and a
72-character measure. Technical detail (hashes, parameter snapshots, raw payloads) sits one
disclosure away, never on the first line, and never hidden when it changes interpretation.

The system rejects the category reflexes of security tooling: dark terminals with neon, threat-map
globes, glass panels, glowing accents, hero metrics and invented confidence scores. It also rejects
consumer chat styling for AI: answers are documents with claims and citations, not bubbles.

The light theme is primary. The physical scene is an analyst reading source material and
cross-checking citations for hours at a desk in ordinary office or daylight lighting, often on a
laptop beside an external monitor; dark text on warm paper is the most comfortable long-form
reading surface there. A dark theme (warm charcoal, same roles and contrast floors) follows the
operating system preference and can be chosen explicitly for evening work.

**Key Characteristics:**

- Warm archive-paper neutrals, one ink-blue accent under 10% of any screen.
- IBM Plex Sans for everything, IBM Plex Mono only for identifiers, hashes and raw values.
- Flat, hairline-bordered panels; no shadows at rest.
- Every status pairs color, icon and words.
- Provenance labels on every piece of material: collected, imported, extracted, OCR, analyst,
  AI-generated, synthetic.

## 2. Colors

A restrained palette: tinted neutrals carry the surface, one accent carries action, semantic
colors carry outcomes. All tokens are defined in OKLCH in `apps/web/src/app/globals.css`; hex values
below are sRGB approximations for reference.

### Primary

- **Iron-Gall Ink** (`oklch(0.48 0.125 262)`, about #355ba3): primary buttons, links, the active
  navigation item, focus rings, the selected row indicator and selected citations. Dark theme:
  `oklch(0.76 0.1 262)`. Its strong variant (`oklch(0.41 0.115 262)`) is the hover and pressed state;
  its soft variant (`oklch(0.95 0.022 262)`) backs the active nav item and selected rows.

### Secondary

- **Annotation Violet** (`oklch(0.49 0.13 300)`, about #6d4c9e): used only to label AI-generated
  content (answers, summaries, relationship suggestions, AI-origin records). It never styles
  controls, so "violet" always means "a model wrote this".

### Neutral

- **Archive Paper** (`oklch(0.975 0.004 85)`, about #f8f7f4): the page canvas.
- **Clean Sheet** (`oklch(0.995 0.002 85)`, about #fefdfc): panels, tables, inputs.
- **Well** (`oklch(0.962 0.005 85)`, about #f4f2ef): table headers, code and preview wells, hover
  fills.
- **Binding** (`oklch(0.955 0.006 85)`, about #f2f0ec): the sidebar, a second neutral layer.
- **Hairline** (`oklch(0.9 0.007 85)`, about #e0ded9): dividers and panel borders.
- **Control Edge** (`oklch(0.65 0.01 85)`): input and checkbox borders, at least 3:1 against
  surfaces.
- **Ink Black** (`oklch(0.235 0.012 80)`, about #211e18): body text, 16:1 on Clean Sheet.
- **Pencil** (`oklch(0.47 0.013 80)`, about #5f5a53): secondary text, labels and metadata, at least
  6:1 on every light surface.
- **Faint Pencil** (`oklch(0.6 0.011 80)`): placeholders and disabled text only.

### Semantic

- **Ledger Green** (`oklch(0.48 0.1 150)`): completed, verified, available. Never used for "no
  findings" alone; a completed run without findings is neutral.
- **Amber Ochre** (`oklch(0.5 0.105 62)`): partial, needs input, rate limited, access required,
  synthetic and fixture-tested labels.
- **Seal Red** (`oklch(0.5 0.165 27)`): failed, integrity mismatch, destructive actions.

Each semantic color has a soft fill and a line variant; text on its soft fill stays above 5.5:1 in
light and 7:1 in dark.

### Named Rules

**The One Voice Rule.** Iron-Gall Ink covers at most 10% of any screen. If two primary buttons are
visible in one panel, one of them is wrong.

**The Violet Means Model Rule.** Annotation Violet appears only on AI-generated material. Nothing
else may borrow it, and AI material may not appear without it.

**The Unknown Is Not Green Rule.** Success color is earned by a verified, complete outcome. Partial,
blocked, unknown and synthetic states use amber or neutral, never green.

## 3. Typography

**Body Font:** IBM Plex Sans Variable (bundled from `@fontsource-variable/ibm-plex-sans`, with
system-ui fallback)
**Mono Font:** IBM Plex Mono 400 and 500 (bundled from `@fontsource/ibm-plex-mono`)

**Character:** Plex is an engineered grotesque with open apertures and clearly distinguished `I l 1`
and `O 0`, which matters when reading usernames, domains and hashes. It covers Latin Extended, so
Turkish `ı İ ş ğ` render natively. Fonts are served from the application itself; no font server is
contacted.

### Hierarchy

- **Title** (600, 1.375rem/1.75rem, -0.01em): the single `h1` of each page. Never larger.
- **Heading** (600, 1rem/1.5rem): panel and section headings (`h2`).
- **Subheading** (600, 0.875rem/1.25rem): groups inside a panel (`h3`).
- **Body** (400, 0.875rem/1.25rem): interface text, table cells, form controls.
- **Read** (400, 0.9375rem/1.5rem, max 72ch): evidence previews, chat lines, AI claims, notes,
  case purpose and scope.
- **Label** (500, 0.75rem/1rem): field labels in dense filters, table headers (sentence case, not
  uppercase), metadata keys, status labels.
- **Mono** (400, 0.8125rem/1.25rem): identifiers, hashes, connector IDs, raw JSON. Dates are not
  mono; they use Body with tabular figures.

### Named Rules

**The Mono Is Evidence Rule.** Monospace marks values that must be read character by character:
hashes, identifiers, JSON pointers, raw payloads. Timestamps, counts and labels stay in the sans
face with `font-variant-numeric: tabular-nums`.

**The No Giant Header Rule.** Nothing exceeds the Title size. Hierarchy comes from weight, spacing
and position, not scale.

## 4. Elevation

Flat by default. Depth comes from tonal layering (Binding sidebar, Archive Paper canvas, Clean Sheet
panels, Well insets) and 1px hairlines. Shadows appear only on surfaces that float above the page
because of an interaction: the mobile navigation drawer, menus and popovers.

### Shadow Vocabulary

- **Overlay** (`box-shadow: 0 12px 32px -8px oklch(0.2 0.02 80 / 0.22), 0 2px 6px oklch(0.2 0.02 80 / 0.08)`):
  the navigation drawer below 1024px and the account menu.

### Named Rules

**The Flat-At-Rest Rule.** Panels, tables, cards and badges never carry a shadow. If something
needs to stand out at rest, it earns a border, a fill or a position, not a shadow.

## 5. Components

Refined and restrained: familiar shapes, consistent heights, visible states.

### Buttons

- **Shape:** 6px radius; heights 36px (default) and 28px (compact, in tables and toolbars).
- **Primary:** Iron-Gall Ink fill, near-white text, 14px horizontal padding. One per panel.
- **Secondary:** Clean Sheet fill, Control Edge border, Ink text. Hover fills Well.
- **Ghost:** no border, Pencil text, Well fill on hover. For toolbar and inline actions.
- **Danger:** Seal Red fill, used only on the confirming step of a destructive action.
- **States:** hover darkens by one step (150ms ease-out); focus shows a 2px Iron-Gall ring with 2px
  offset; disabled drops to 55% opacity with `not-allowed`; busy keeps the label, sets
  `aria-busy` and shows a small spinner icon.

### Status labels

- **Style:** 4px radius, soft semantic fill, semantic line border, 12px medium text, 14px leading
  icon (check, clock, alert, cross, pause, flask).
- **Vocabulary:** completed with findings, completed without findings (neutral), partial,
  failed, cancelled, queued or running (neutral with animated dots disabled under reduced motion),
  needs input, access or configuration required, rate limited.

### Provenance labels

- **Style:** neutral outlined chip with a leading icon; the icon and words carry the meaning.
- **Set:** Collected (globe), Authorized import (upload), Extracted text (file-text), OCR text
  (scan), Analyst assertion (pen), Observed (eye), AI-generated (sparkle, Annotation Violet),
  Synthetic (flask, Amber Ochre), Fixture-tested (flask, Amber Ochre).

### Panels

- **Corner Style:** 8px radius.
- **Background:** Clean Sheet on Archive Paper.
- **Border:** 1px Hairline. Header row separated by a Hairline, 12px by 16px padding.
- **Shadow Strategy:** none (see Elevation).
- Panels are never nested inside panels; inner grouping uses Subheadings and dividers.

### Tables

- Header row on Well, Label typography in sentence case, sticky within its scroll container.
- Rows 40px minimum with a Hairline divider; hover fills Well; the selected row gets the soft
  accent fill plus `aria-selected` or `aria-current`.
- Wide tables scroll horizontally inside their container; the page never scrolls sideways.
- Long values (URLs, hashes, filenames) truncate with an ellipsis, expose the full value on focus
  or hover, and offer copy.

### Inputs and fields

- **Style:** 36px height, Clean Sheet fill, 1px Control Edge border, 6px radius.
- **Focus:** border and 2px ring in Iron-Gall Ink.
- **Error:** Seal Red border with the message below, linked by `aria-describedby`.
- **Labels:** always visible above the control; hints below in Pencil.
- File inputs state accepted formats and size limits before a file is chosen.

### Navigation

- **Sidebar (≥1024px):** 248px, Binding fill, right Hairline. Groups: Workspace (Overview, Cases),
  the current case (Overview, Collect, Examine, Analyze, Report) when inside one, and Configuration
  (Sources, Environment status, Preferences). Items are 32px, Pencil text with an icon; the active
  item uses Iron-Gall Ink text on the soft accent fill with `aria-current="page"`.
- **Header:** 52px, sticky, Clean Sheet with bottom Hairline: menu button (small screens),
  breadcrumbs, account menu.
- **Below 1024px:** the sidebar becomes a focus-trapped drawer opened from the header, closed by
  Escape, the backdrop or navigation.
- **Tabs:** underline style, 2px Iron-Gall indicator, used for switching views inside one page (for
  example timeline sections or import types).

### Empty, loading and error states

- **Empty:** a short sentence that says what belongs here and the one action that creates it.
- **Loading:** skeleton rows shaped like the content, announced once with `role="status"`.
- **Error:** Seal Red soft notice with what failed, what it means and a Retry action.

### Citation chip

Mono `[E1]` label in a 4px-radius outlined chip; opens the citation panel beside the answer; the
selected chip fills with Iron-Gall Ink. Its accessible name states the source.

## 6. Do's and Don'ts

### Do:

- **Do** pair every status color with an icon and words ("Partial", "Needs your input").
- **Do** label provenance on every evidence row and every AI-generated block.
- **Do** keep one primary action per panel and put page-level primary actions in the page header.
- **Do** show times with their basis: UTC, local time with unknown timezone, or collection time.
- **Do** use IBM Plex Mono only for identifiers, hashes and raw values.
- **Do** cap prose at 72ch and keep reading text at 0.9375rem.
- **Do** keep wide tables and the graph in their own scroll containers.
- **Do** honor `prefers-reduced-motion` and keep transitions at 150 to 200ms with ease-out.

### Don't:

- **Don't** use dark terminals, neon green or cyan on black, glitch effects or threat-map globes.
- **Don't** add hero metrics, big-number KPI cards, fabricated scores or confidence percentages.
- **Don't** use glassmorphism, blurred backdrops, glows or decorative gradients.
- **Don't** style AI answers as chat bubbles with avatars.
- **Don't** imply that a matching username or a chat sender label establishes identity.
- **Don't** use `border-left` or `border-right` wider than 1px as a colored accent stripe.
- **Don't** nest panels inside panels or build grids of identical icon cards.
- **Don't** use green for partial, unknown, synthetic or fixture-tested states.
- **Don't** exceed the Title size (1.375rem) anywhere in the application.
- **Don't** use em dashes in interface copy.
- **Don't** load fonts, scripts or images from third-party servers.
