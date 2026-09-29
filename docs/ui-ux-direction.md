# Deal Rescue AI — UI/UX direction

Status: **direction only**. Nothing in this document is implemented yet except where §1 says so.
Every later screen (M2 onward) must follow it; deviations need a short note in the PR explaining why.

---

## 1. Current frontend inventory (2026-09-28)

| File | What it is | Fate |
|---|---|---|
| `frontend/src/App.tsx` | M0 **status page**: fetches `/api/health`, shows loading / ok / error | Keep until the app shell exists; then move into a small "System status" panel in Settings |
| `frontend/src/index.css` | M0 placeholder variables (`--bg`, `--fg`, `--ok`, …) and 5 classes | Replace with `src/styles/tokens.css` + `base.css` from §8 when M2 UI work starts |
| `frontend/src/main.tsx` | Standard React 19 entry | Keep |
| `frontend/index.html` | Title already "Deal Rescue AI" | Keep; add font preload later (§4.3) |
| `frontend/public/favicon.svg` | **Vite's default logo** (starter asset) | Replace with the Deal Rescue mark (§4.6) |
| `frontend/vite.config.ts` | Dev server 127.0.0.1:5180, `/api` proxy → 8010 | Keep |
| `.oxlintrc.json`, `tsconfig*.json`, `package.json` | Scaffold config; no UI libraries installed | Keep |

Nothing product-related exists in the frontend. There is no router, no component library, no
state library. This document does not change that.

---

## 2. The idea: a case file, not a dashboard

Deal Rescue AI is used when a deal is going wrong and a rep needs to decide what to do next. The
closest real-world analogue is not a BI dashboard; it is an **investigator's case file**: a dated
record of what happened, who said what, what we suspect, and what we might try — each kept
visibly separate.

Design concept: **"The Evidence Desk."** Warm paper surfaces, ink typography, hairline rules,
ledgers and timelines. Colour is reserved almost entirely for *epistemic status* (how we know
something) and for *attention* (what needs action). The interface should read like a well-edited
briefing document that happens to be interactive.

### 2.1 Design principles

1. **Provenance before polish.** Every claim on screen shows how we know it: recorded, stated,
   inferred, or hypothetical. If the UI cannot say where a sentence came from, the sentence does
   not ship.
2. **Time is the primary axis.** Deals and customers are histories. Default to chronological
   structures (timelines, dated ledgers, version chains), not snapshots.
3. **Sentences over widgets.** Prefer "3 deals need action; 2 have unresolved budget conflicts"
   to three number tiles. Numbers appear inside context, in tabular mono.
4. **Absence is information.** Missing evidence, stale memory and unrecorded outcomes are shown
   explicitly with the same care as present data. Never fill gaps with plausible filler.
5. **Rules are quiet, attention is loud.** Structure comes from typography and hairlines; the
   single signal colour appears only where the user must act.
6. **The AI is a colleague who cites sources, not a chat window.** Generated text appears inline
   in the document it belongs to, marked as inference, with its citations and a way to inspect them.
7. **Density with calm.** Sales reps scan a lot of history. Use compact rows and generous margins,
   not big cards with little in them.

---

## 3. Prohibited patterns

These are explicitly out of bounds. Reviewers should reject them.

- **Generic SaaS dashboard grid**: a row of KPI cards on top, charts below, a table at the bottom.
- **Metric cards** that show a lone number without the sentence that makes it actionable
  (no "Total pipeline $1.2M ↑12%" tiles). A number may appear only if the user can act on it or
  it is evidence for a claim.
- **Card soup**: nesting content in rounded, shadowed boxes. Max radius is 2px on controls; panels
  have **no** radius and **no** shadow (§6.4).
- **Decorative gradients, glassmorphism, glows, blobs, mesh backgrounds, or illustration fillers.**
  A gradient is allowed only if it encodes data (none are planned).
- **Chatbot-first layouts**: a chat panel as the main surface, "Ask AI anything" hero inputs,
  sparkle ✨ icons, typing indicators pretending to be a person, or AI output in chat bubbles.
- **Win-probability meters, confidence percentages, health scores, gauges or traffic-light
  "deal scores."** The product has no validated predictive model; showing one would be dishonest.
- **Colour-only meaning.** Every status colour has a paired label, glyph or line style.
- **Default AI-tool aesthetics**: Inter/system font everywhere, indigo-500 primary buttons,
  `rounded-2xl shadow-lg` cards, emoji section headers, centered empty states with a cartoon.
- **Toasts for important outcomes.** Anything the user needs to remember (e.g. "memory stored",
  "outcome recorded") is written into the page's ledger, not a disappearing toast.
- **Infinite spinners.** Every async state has a text label and a timeout/error path.
- **Pie/donut charts, 3D, dual-axis charts.** Charts are rare; see §6.9.

---

## 4. Visual identity

### 4.1 Mood

Editorial, forensic, calm. References (for tone, not copying): a newspaper's archive desk, a
legal case bundle, an air-accident investigation report, a well-typeset annual report's notes
section.

### 4.2 Colour

All pairs below were checked against WCAG 2.2 (computed on 2026-09-28): body and
evidence text ≥ 4.5:1 on every surface in both themes; `rule-strong` ≥ 3:1 for control
boundaries.

**Neutrals — "Paper" (light) / "Night desk" (dark)**

| Token | Light | Dark | Use |
|---|---|---|---|
| `--c-paper` | `#F4F1EA` | `#171614` | App canvas |
| `--c-raised` | `#FBFAF6` | `#1F1E1B` | Reading surfaces, inspector, drawers |
| `--c-sunken` | `#ECE7DC` | `#121110` | Rails, input wells, table header band |
| `--c-ink` | `#1B1A17` | `#ECE8DF` | Primary text |
| `--c-ink-2` | `#48453E` | `#BFB9AC` | Secondary text |
| `--c-ink-3` | `#6A665C` | `#9C968A` | Meta: timestamps, ids, captions (min contrast 4.64:1) |
| `--c-rule` | `#D8D2C5` | `#34322D` | Hairline separators (decorative, not boundaries) |
| `--c-rule-strong` | `#8A8273` | `#716C62` | Input borders, focusable boundaries (≥3:1) |

**Evidence colours (the only "brand" colours)**

| Evidence kind | Token | Light | Dark | Tint (bg) light / dark |
|---|---|---|---|---|
| Recorded fact | `--c-recorded` | `#1F3A5F` ink-blue | `#93B6E2` | `#E6ECF3` / `#1D2633` |
| Customer statement | `--c-statement` | `#1D5C50` deep teal | `#7FC8B6` | `#E3EFEB` / `#1A2A26` |
| Model inference | `--c-inference` | `#855300` ochre | `#E2B660` | `#F5EBD6` / `#2E2616` |
| Hypothetical scenario | `--c-hypothetical` | `#5A3D8A` violet | `#BBA5EC` | `#ECE6F4` / `#262035` |

**Attention and system**

| Token | Light | Dark | Use |
|---|---|---|---|
| `--c-signal` | `#AD2F1A` vermilion | `#F08A6E` | Needs action: overdue, stalled, conflict. Never decorative |
| `--c-signal-tint` | `#F7E3DE` | `#34201B` | Background of a conflict/overdue row |
| `--c-ok` | `#2E6A38` | `#8BCB93` | Confirmed success states (retained, saved, won) |
| `--c-focus` | `#1E5BB8` | `#8AB4FF` | Focus ring only |

Rules:
- Evidence colours are used for **marks** (left rule, label, citation chip) and optionally their tint as
  a row background. Body text inside evidence blocks stays `--c-ink`.
- `--c-signal` appears at most in a handful of places per screen. If everything is urgent, nothing is.
- Won/lost outcomes use neutral ink plus a label ("Won", "Lost", "No decision"); lost is not red.

### 4.3 Typography

Three families, all open-licence (SIL OFL), self-hosted as WOFF2 (no runtime Google Fonts request, so
the demo works offline):

| Role | Family | Why |
|---|---|---|
| Display & narrative (page titles, briefing prose, quotations) | **Newsreader** (variable, opsz) | Editorial serif with optical sizes; gives the "briefing document" voice |
| Interface (labels, tables, controls) | **IBM Plex Sans** | Neutral, highly legible at small sizes, not the default AI-tool font |
| Data (money, dates, ids, counts, memory ids) | **IBM Plex Mono** | Tabular alignment, reads as "recorded" |

Loading: add `@fontsource-variable/newsreader`, `@fontsource/ibm-plex-sans` (400/500/600) and
`@fontsource/ibm-plex-mono` (400/500) when UI work starts. These are font files, not UI libraries, and
are the **only** frontend dependencies this direction allows by default. Fallbacks:
`Georgia, 'Times New Roman', serif` / `'Segoe UI', system-ui, sans-serif` / `ui-monospace, Consolas, monospace`.

Type scale (rem, 1rem = 16px; ratio ≈1.2, tuned):

| Token | Size / line-height | Family / weight | Use |
|---|---|---|---|
| `--t-display` | 2.25 / 1.15 | Newsreader 500, opsz auto | Page title (one per page) |
| `--t-h1` | 1.625 / 1.2 | Newsreader 500 | Section titles ("Situation", "Commitments") |
| `--t-h2` | 1.125 / 1.3 | Plex Sans 600 | Sub-sections, panel titles |
| `--t-body` | 1 / 1.55 | Plex Sans 400 | Default UI text |
| `--t-prose` | 1.0625 / 1.6 | Newsreader 400 | Briefings, generated narrative, quotes |
| `--t-small` | 0.875 / 1.45 | Plex Sans 400 | Table cells, secondary text |
| `--t-meta` | 0.75 / 1.4 | Plex Mono 500, `letter-spacing: .04em`, uppercase | Evidence labels, column headers |
| `--t-data` | 0.875 / 1.45 | Plex Mono 400, `font-variant-numeric: tabular-nums` | Money, dates, ids |

Rules: max line length 72ch for prose; never all-caps sentences (only `--t-meta` labels); money always
mono with currency code (`USD 42,000`); dates as `12 Sep 2026` in UI and ISO in tooltips.

### 4.4 Spacing & grid

- Base unit 4px. Scale: `--s-1` 4, `--s-2` 8, `--s-3` 12, `--s-4` 16, `--s-5` 24, `--s-6` 32, `--s-7` 48, `--s-8` 64.
- Row heights: dense ledger row 36px, comfortable 44px; min touch target 24×24 (WCAG 2.5.8), 44×44 on touch layouts.
- Page gutter: 16px (<768), 24px (768–1279), 32px (≥1280).
- Layout is a **12-column grid** on wide screens, but screens are composed as *panes* (§5), not card grids.

### 4.5 Lines, shape, elevation

- Separation via **hairlines** (`1px solid var(--c-rule)`) and whitespace, not boxes.
- Radius: `--r-0: 0` for panes, tables, drawers; `--r-1: 2px` for inputs, buttons, chips. Nothing else.
- Shadow: only overlays (drawer, popover, command palette) get `--shadow-overlay`. Panes are flat.
- Evidence blocks use a **3px left rule** in the evidence colour (the product's signature mark).

### 4.6 Iconography & mark

- Icons: a small hand-picked inline-SVG set (stroke 1.5px, 16/20px grid) kept in `src/ui/icons/`.
  No icon-font or 1,000-icon library.
- Glyphs carry meaning alongside colour: ■ recorded, ❝ statement, ◇ inference, ⧉ hypothetical
  (drawn as SVG, not emoji), ⚠ conflict, ◷ stale.
- Logo mark: a simple monogram "DR" set in Newsreader inside a 2px-ruled square with a single
  vermilion tick — used for favicon and app rail. (To be drawn when the shell is built.)

---

## 5. Navigation & information hierarchy

### 5.1 App shell

```
┌──────┬─────────────────────────────────────────────────────────────┐
│ Rail │ Case path: Workspace / Aurora Logistics / Pilot expansion    │
│      ├─────────────────────────────────────────┬───────────────────┤
│  W   │                                         │                   │
│  C   │           Primary pane                  │  Inspector pane   │
│  D   │     (the document you are reading)      │ (evidence, detail │
│      │                                         │  of selection)    │
│  ⌘K  │                                         │                   │
└──────┴─────────────────────────────────────────┴───────────────────┘
```

- **Rail (56px)**: Workspace, Customers, Deals, and Settings/System status at the bottom. Icons + text
  label on hover/focus; keyboard `g w`, `g c`, `g d`.
- **Case path** (breadcrumb) always visible; it is the user's sense of place.
- **Command palette (⌘K / Ctrl+K)**: jump to customer/deal, record interaction, open Time Machine.
  It is navigation, not a chatbot.
- **Deal Time Machine is entered from a deal**, never from the rail — it only makes sense with a deal.
- **Inspector** is where citations open. Clicking any citation fills the inspector; it never navigates away.

### 5.2 Hierarchy on every screen

1. *What is this?* — title, entity, key facts in one line (mono data).
2. *What needs my attention?* — signal items, at most three, each a sentence with a citation.
3. *What happened?* — timeline / ledger.
4. *What could I do?* — actions, drafts, scenarios (always visually hypothetical until acted on).

---

## 6. Component patterns

### 6.1 Evidence taxonomy (the core pattern)

Every piece of content belongs to exactly one kind. The kind drives colour, glyph, line style,
label text and typography — so meaning survives greyscale, colour-blindness and screen readers.

| Kind | Source of truth | Mark | Label (visible) | Typography | Extra |
|---|---|---|---|---|---|
| `recorded` | SQL record or recorded outcome | solid 3px ink-blue rule, ■ | `RECORDED · 12 Sep 2026` | Plex Sans | Links to the source record |
| `statement` | A quote stored from an interaction | solid 3px teal rule, ❝ | `STATED BY Priya Raman, CFO · 03 Sep` | Newsreader, quotation marks | Verbatim only; never paraphrased under this label |
| `inference` | Model output or rule-derived hypothesis | **dashed** 3px ochre rule, ◇ | `INFERENCE · not confirmed` | Plex Sans | Must list its citations; "Why?" disclosure |
| `hypothetical` | Time Machine scenario / draft | **hatched** background + violet rule, ⧉ | `SCENARIO · exploratory` | Plex Sans | Never shown outside Time Machine or drafts |

Modifiers (combine with any kind):

| State | Visual | Label |
|---|---|---|
| `stale` | ink-3 text + ◷ | `STALE · last confirmed 94 days ago` |
| `superseded` | struck-through value, kept visible, arrow to newer version | `SUPERSEDED 12 Sep by …` |
| `conflict` | signal tint row + ⚠ + double rule | `CONFLICT · needs resolution` with Resolve action |
| `missing` | dotted outline, empty | `NO EVIDENCE RECORDED` — plain statement, never filler text |

A hidden objection is always an `inference`. It can become a `statement` only when a real quote is
recorded; the UI must never "promote" it automatically.

### 6.2 Citations

- Inline chips after the sentence they support: `[R3]` `[S1]` `[I2]` (prefix = kind), mono, 2px radius,
  coloured by kind. Multiple chips allowed; zero chips on an inference = render "No supporting evidence" in ink-3.
- Hover/focus shows a preview popover (first 2 lines + date); click/Enter opens it in the Inspector.
- Server-rejected citations (ids not in the retrieved set) render as a struck chip with tooltip
  "Citation removed: not found in retrieved evidence".

### 6.3 Ledgers (tables)

- Semantic `<table>` with sticky `--t-meta` header on a sunken band; rows separated by hairlines; no zebra.
- Numeric and date columns right-aligned, mono, tabular numbers.
- Row-level status is a leading glyph + label, not a coloured pill.
- Sorting is explicit (header button with `aria-sort`); default sort is the rule that matters
  (e.g. commitments by due date, overdue first).

### 6.4 Panes, sections, disclosure

- A **Pane** is a full-height region separated by a hairline. No card frames.
- A **Section** = `--t-h1` serif title + optional meta line + content; sections are separated by 48px and a hairline.
- Long content uses `<details>`-style disclosure with a text affordance ("Show 6 earlier interactions").

### 6.5 Timeline

- Vertical by default (dated rows: date column in mono | kind glyph | content | citations).
- Items grouped by day, with a day heading; the gap between entries reflects nothing (no fake scale) except
  in the Time Machine, where the horizontal axis *is* scaled to real dates.
- Keyboard: ↑/↓ moves between items, Enter opens in Inspector.

### 6.6 Version chains (memory evolution)

For a changing requirement (budget, seats, deadline):

```
BUDGET   USD 42,000 ── 03 Sep [S1]  →  USD 35,000 ── 12 Sep [S4]   (current)
         ~~superseded~~
```

Old values stay visible (struck through, ink-3), newest on the right, each with its citation.
Unresolved disagreements show as a fork with ⚠ instead of an arrow.

### 6.7 Actions & forms

- Primary action: ink-filled rectangle (radius 2px), Plex Sans 500. One per section at most.
- Secondary: text button with underline on hover. Destructive: signal text, confirmation required.
- Forms are single-column, labels above inputs, help text below, server errors inline next to the field.
- Anything that writes to memory states it: "Save interaction — will be stored in customer memory".

### 6.8 Async, empty and error states

| State | Pattern |
|---|---|
| Loading | Skeleton **lines** matching final layout + text "Retrieving customer memory…" |
| Memory processing | Inline ledger entry "Stored in record · memory processing (Hindsight)" → "Memory ready" |
| Empty | One sentence on what is missing + the action that fixes it ("No interactions recorded yet. Record the first call.") |
| Error | What failed, whether data was saved, what to do: "Saved to deal record. Memory service unavailable — retry storing to memory." |
| Degraded | If Hindsight or Claude is down, affected sections show a ruled notice; the rest of the page still works |

### 6.9 Charts

Allowed only where time or comparison is the point: activity sparkline on a deal (interactions per week),
the Time Machine timeline. Use SVG drawn in-house; follow the evidence colours; label directly (no legends
when avoidable). No charts on the Workspace.

---

## 7. Screen experiences

### 7.1 Intelligence Workspace (home)

Purpose: "What should I deal with today, and why?"

- **Opening line** (serif, `--t-prose`): a factual sentence built deterministically, e.g.
  *"7 open deals. 3 need action: 2 overdue commitments, 1 stalled 19 days. 2 memory conflicts await resolution."*
- **Docket** (main ledger): deals needing attention, ranked by deterministic rules (overdue commitments,
  days since last contact vs stage threshold, unresolved conflicts, approaching close date). Each row:
  deal · customer · stage · value (mono) · *reason sentence* · evidence chips · "Open".
  The reason explains the rule ("No contact for 19 days; stage threshold is 14"), never a score.
- **Since you were last here**: a short ledger of what changed (new interactions, requirement changes,
  outcomes recorded), each with its kind mark.
- **Conflicts queue**: memory conflicts/stale items needing a human decision, with Resolve.
- No KPI tiles, no pipeline charts, no AI chat box.

### 7.2 Customer Memory Explorer

Purpose: "What do we know about this customer, since when, and how do we know it?"

Three panes:
1. **Index** (left, 280px): customers list with search; then, for the selected customer, stakeholders and
   topic filters (Budget, Requirements, Stakeholders, Commitments, Objections).
2. **Memory timeline** (centre): the customer's history as dated entries of all four kinds, with
   **version chains** pinned at the top for key facts (budget, requirements, decision date). Filters:
   evidence kind, date range, "show superseded", "show stale". An **As-of** date control re-renders the
   page as it was known on that date (grey banner: "Viewing memory as of 03 Sep 2026").
3. **Inspector** (right, 360px): the selected memory's provenance — kind, source interaction (link to the
   original note), dates (occurred / recorded), Hindsight memory id (mono, copyable), tags, and related
   memories. For inferences: the citations that produced them.

Memory evolution is the hero: a budget change reads as a chain, not an overwritten field.

### 7.3 Deal Detail

Purpose: "What is the state of this deal and what's at risk?"

- **Case header**: deal title (serif display) · customer · stage · value (mono) · close date · owner · last
  contact ("19 days ago", signal if over threshold). A thin **stage rail** shows stage history with dates.
- **Situation** (briefing, serif prose): 3–6 sentences, each with citations; inferences inline but marked.
  "Regenerate" shows the generation time and which memories were used.
- **Attention**: at most three signal items (overdue commitment, stall, conflict, possible hidden objection
  as inference).
- **Commitments** ledger: promise · owner (us/customer) · due · status · source interaction.
- **Stakeholders** (lightweight): a compact matrix — name, role, stated priorities (statements),
  inferred stance (inference) — and conflicting requirements shown as pairs with ⚠.
- **Interaction timeline**.
- **Comparable deals**: other deals with *recorded outcomes* only, each with outcome, the actions recorded,
  and why it's comparable (shared tags/entities). If none: "No comparable deals with recorded outcomes yet."
- **Actions** (bottom bar on narrow screens, header on wide): Record interaction · Draft follow-up ·
  Record outcome · **Open Time Machine**.
- Follow-up drafts render as `hypothetical` until the user copies/sends them outside the app (no sending).

### 7.4 Deal Time Machine (signature)

Purpose: "Given what actually happened, and what happened in similar deals, how do these options compare?"

Layout, top to bottom:

1. **Framing banner** (ruled, not a toast): *"Exploratory comparison. Scenarios are not predictions; they
   show what the recorded evidence does and does not support."*
2. **The timeline** (full width, horizontal, real date scale):
   - Left of **NOW**: the recorded past as a solid ink line with interaction ticks, requirement-change
     markers and stage changes. Hover shows the entry; click opens it in the Inspector.
   - A **scrubber** can be dragged into the past: the page shows "what we knew on that date" (as-of view),
     so the user can see when the deal started to slip.
   - Right of **NOW**: 2–4 **branches**, one per strategy, drawn as violet hatched/dashed lines fanning out.
     Branches have no length meaning (no fake timeline into the future) and no heights/scores.
3. **Strategy comparison matrix** (the core), rows = strategies, columns:
   - *What the strategy is* (short, `hypothetical`)
   - *Supporting evidence* — citations to this deal's memories and comparable deals' recorded outcomes
   - *Contradicting evidence* — same, explicitly
   - *Evidence gaps* — what we don't know that matters ("No recorded response from Procurement")
   - *Commitments it would create*
   Each cell is text + chips; a cell with no evidence says so in ink-3. No ranking, no percentages.
   A neutral verdict per row, derived from the counts and wording rules: "Supported by recorded evidence",
   "Mixed evidence", "Unsupported — no comparable outcomes".
4. **Comparable deals strip**: the historical deals used, each with its recorded outcome and the action
   that preceded it, so the user can judge relevance themselves.
5. **Decide & record**: user can mark a strategy as chosen (creates a recommendation record) and later
   record the actual outcome — the loop that makes future comparisons better. The UI shows when a
   comparison was informed by an outcome recorded after an earlier run ("New since last run: Borealis — Lost").

Interaction: keyboard ←/→ moves the scrubber by interaction; Tab moves through branches → matrix rows;
the matrix is a real table for screen readers; the timeline has a text alternative (a dated list).

---

## 8. Design tokens & code conventions (React + TypeScript + CSS)

No CSS framework, no component library, no CSS-in-JS runtime. Plain CSS custom properties +
**CSS Modules** (built into Vite, nothing to install).

### 8.1 Files

```
frontend/src/
  styles/
    tokens.css        # all custom properties below; the only place raw hex values may appear
    base.css          # reset, element defaults, font-face imports, focus style, reduced motion
  ui/                 # design-system primitives (no data fetching)
    EvidenceMark/ EvidenceMark.tsx  EvidenceMark.module.css
    Citation/ Ledger/ Timeline/ VersionChain/ Section/ Pane/ Inspector/ StatusNotice/ Button/ icons/
  features/           # screens: workspace/, customers/, deals/, time-machine/
  lib/evidence.ts     # EvidenceKind types + label/glyph helpers
```

### 8.2 Tokens (`tokens.css` — to be created when M2 UI starts)

```css
:root {
  /* neutrals */
  --c-paper: #F4F1EA;  --c-raised: #FBFAF6;  --c-sunken: #ECE7DC;
  --c-ink: #1B1A17;    --c-ink-2: #48453E;   --c-ink-3: #6A665C;
  --c-rule: #D8D2C5;   --c-rule-strong: #8A8273;
  /* evidence */
  --c-recorded: #1F3A5F;     --c-recorded-tint: #E6ECF3;
  --c-statement: #1D5C50;    --c-statement-tint: #E3EFEB;
  --c-inference: #855300;    --c-inference-tint: #F5EBD6;
  --c-hypothetical: #5A3D8A; --c-hypothetical-tint: #ECE6F4;
  /* attention & system */
  --c-signal: #AD2F1A;  --c-signal-tint: #F7E3DE;  --c-ok: #2E6A38;  --c-focus: #1E5BB8;

  /* type */
  --f-serif: 'Newsreader Variable', Georgia, 'Times New Roman', serif;
  --f-sans: 'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif;
  --f-mono: 'IBM Plex Mono', ui-monospace, Consolas, monospace;
  --t-display: 500 2.25rem/1.15 var(--f-serif);
  --t-h1: 500 1.625rem/1.2 var(--f-serif);
  --t-h2: 600 1.125rem/1.3 var(--f-sans);
  --t-body: 400 1rem/1.55 var(--f-sans);
  --t-prose: 400 1.0625rem/1.6 var(--f-serif);
  --t-small: 400 0.875rem/1.45 var(--f-sans);
  --t-meta: 500 0.75rem/1.4 var(--f-mono);
  --t-data: 400 0.875rem/1.45 var(--f-mono);

  /* space, shape, motion */
  --s-1: 4px; --s-2: 8px; --s-3: 12px; --s-4: 16px; --s-5: 24px; --s-6: 32px; --s-7: 48px; --s-8: 64px;
  --r-0: 0; --r-1: 2px;
  --rule-w: 1px; --mark-w: 3px;
  --shadow-overlay: 0 8px 24px rgb(27 26 23 / 0.18);
  --dur-fast: 120ms; --dur-base: 180ms; --ease: cubic-bezier(0.2, 0, 0, 1);
  --pane-index: 280px; --pane-inspector: 360px; --rail: 56px;
  --measure: 72ch;
  color-scheme: light;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) { /* same values as [data-theme='dark'] below */ }
}
:root[data-theme='dark'] {
  --c-paper: #171614;  --c-raised: #1F1E1B;  --c-sunken: #121110;
  --c-ink: #ECE8DF;    --c-ink-2: #BFB9AC;   --c-ink-3: #9C968A;
  --c-rule: #34322D;   --c-rule-strong: #716C62;
  --c-recorded: #93B6E2;     --c-recorded-tint: #1D2633;
  --c-statement: #7FC8B6;    --c-statement-tint: #1A2A26;
  --c-inference: #E2B660;    --c-inference-tint: #2E2616;
  --c-hypothetical: #BBA5EC; --c-hypothetical-tint: #262035;
  --c-signal: #F08A6E;  --c-signal-tint: #34201B;  --c-ok: #8BCB93;  --c-focus: #8AB4FF;
  --shadow-overlay: 0 8px 24px rgb(0 0 0 / 0.5);
  color-scheme: dark;
}
```

(When implemented, the dark values are written out in both the media-query block and the
`[data-theme='dark']` block; the comment above is shorthand.)

The hatched scenario background:

```css
.hypothetical { background:
  repeating-linear-gradient(135deg, var(--c-hypothetical-tint) 0 6px, transparent 6px 12px); }
```
(This is the one permitted gradient: it encodes "hypothetical".)

### 8.3 TypeScript conventions

```ts
// lib/evidence.ts
export type EvidenceKind = 'recorded' | 'statement' | 'inference' | 'hypothetical'
export type EvidenceState = 'current' | 'stale' | 'superseded' | 'conflict'

export interface EvidenceRef {
  id: string                 // app evidence id, e.g. "R3"
  kind: EvidenceKind
  label: string              // human text for the chip preview
  recordedAt?: string        // ISO; displayed via formatDate()
  source: { type: 'interaction' | 'outcome' | 'memory' | 'deal'; id: string }
  hindsightMemoryId?: string
}
```

- Components take **data + kind**, never colours: `<EvidenceMark kind="inference" state="stale">`.
  Colour/glyph/label mapping lives only in `lib/evidence.ts` + `EvidenceMark.module.css`.
- `kind` is required wherever content could be ambiguous (TypeScript makes it a compile error to omit).
- Primitives in `ui/` are presentational: no fetching, no business rules. Screens in `features/` compose them.
- Formatting helpers (`formatMoney`, `formatDate`, `formatRelativeDays`) live in `lib/format.ts`; components
  never format numbers inline.
- Class names via CSS Modules (`styles.row`), state via `data-*` attributes
  (`data-kind="statement" data-state="superseded"`) so CSS, tests and a11y tooling can read them.
- No hard-coded hex, px font sizes or ad-hoc spacing in component CSS — tokens only. A lint check
  (grep for `#[0-9a-fA-F]{3,6}` outside `tokens.css`) will be added with the first UI milestone.

---

## 9. Interaction behaviour

- **Motion**: 120–180ms, `--ease`, used for disclosure, drawer entry and scrubber feedback only. No
  bouncing, no parallax, no animated counters. `prefers-reduced-motion: reduce` → transitions off.
- **Focus**: 2px `--c-focus` outline with 2px offset on every interactive element; never removed.
- **Keyboard**: full operation without a mouse. Global: ⌘/Ctrl+K palette, `g w/c/d` navigation, `?` shortcut
  sheet, `Esc` closes inspector/drawer and returns focus to the trigger.
- **Selection model**: single selection drives the Inspector; selection is reflected in the URL
  (`?evidence=S4`) so a view with an open citation is shareable and survives reload.
- **Writes**: optimistic only for the local record; memory storage status is shown truthfully (§6.8).
- **Generated text**: appears in place with the label "Generated 14:02 from 9 memories"; regenerate
  keeps the previous version accessible for comparison.

## 10. Accessibility (target: WCAG 2.2 AA)

- Contrast verified for the palette (§4.2). Re-verify any new colour before use.
- Meaning never by colour alone: every evidence kind/state has a text label and glyph/line style.
- Semantics: landmarks (`nav`, `main`, `aside` for Inspector), real headings in order, tables for ledgers
  and the Time Machine matrix, `aria-sort`, `aria-current` for navigation.
- Live regions: memory-processing status and async errors use `aria-live="polite"`.
- Citations are links/buttons with accessible names ("Citation S1: Priya Raman statement, 3 Sep 2026").
- Timeline and Time Machine visuals have an equivalent dated list; the SVG is `aria-hidden` when a list exists.
- Targets ≥24×24px; zoom to 200% without loss; no horizontal scroll of the page at 320px width except inside
  ledgers/timelines, which scroll in their own region.

## 11. Responsive behaviour

| Width | Layout |
|---|---|
| ≥1280 | Rail + primary + inspector (three panes). Time Machine matrix full table. |
| 1024–1279 | Rail + primary; inspector becomes a right drawer (overlay) on selection. |
| 768–1023 | Rail collapses to a top bar; Customer Memory index becomes a select/search at the top. |
| <768 | Single column, 16px gutters. Workspace docket rows become stacked entries (reason sentence first). Time Machine: timeline becomes a vertical dated list, then one section per strategy with its supporting / contradicting / gaps lists. Inspector is a full-screen sheet. |

Mobile is a **review** surface (read briefings, check evidence, record a quick interaction); heavy
analysis is designed for desktop first. The demo is presented on desktop.

## 12. Copy & tone

- Plain, specific, dated: "Stated by Priya Raman (CFO), 3 Sep 2026" — not "The customer feels…".
- Inferences are phrased as such: "Possible objection: security review may be blocking procurement
  (inferred from 2 statements)". Never "The customer's real objection is…".
- No anthropomorphism ("I think", "Let me help"), no exclamation marks, no emoji.
- Synthetic demo data always carries a visible `SYNTHETIC DEMO DATA` label in the case header.

## 13. Build order for the UI (proposal, for later milestones)

1. `tokens.css`, `base.css`, fonts, favicon mark; replace M0 status page styling.
2. Primitives: EvidenceMark, Citation, Section, Pane, Ledger, StatusNotice, Inspector.
3. App shell (rail, case path, inspector wiring, command palette last).
4. Deal Detail (first real screen, M2–M3 data) → Customer Memory Explorer (M3) → Workspace (M4) →
   Time Machine (M7).
