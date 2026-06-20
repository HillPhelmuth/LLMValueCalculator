---
name: dense-ui
description: Audit a UI for wasted space and hidden data, then redesign it to maximize information density using simple, space-efficient patterns. Framework-agnostic — works whether the UI is Angular, React, Blazor, Vue, Svelte, or plain HTML. Use this skill whenever the user wants to make a UI denser, fit more data on screen, reduce scrolling, tighten a layout, surface data that exists in code but isn't shown, replace bulky cards with tables, or otherwise pack more information into the same space. Trigger it even when the user says things like "this screen feels empty," "too much whitespace," "I'm showing 5 fields but the API returns 40," "make this dashboard tighter," "condense this," or "why am I scrolling so much" — they're asking for density work even if they don't say "density." Also trigger when reviewing a component and the obvious win is information density rather than a feature change.
---

# Dense UI

Make a UI show more useful data in less space, without adding complexity. This skill runs in two phases: an **audit + spec** phase that's framework-agnostic, then a **rewrite** phase that produces real markup and CSS in the target framework.

The user has already decided density is the goal. Don't relitigate it. Your job is to find where space and data are being wasted and fix it with the simplest pattern that works.

## The density philosophy

Three numbers govern every decision:

1. **Data-available** — every field the component *could* show (props, API response, view model, bound model). Much of this is usually thrown away.
2. **Data-displayed** — what the user actually sees right now.
3. **Pixels-per-datum** — screen area divided by data-displayed. Lower is the goal.

The win is almost always one of: surface data that's already in scope but unrendered, or shrink the area each datum occupies, or both. You are raising data-displayed and lowering pixels-per-datum at the same time.

**Density beats accessibility when they conflict — but you always name the tradeoff.** If a fix pushes a tap target below ~44px, drops line-height into cramped territory, or tightens contrast, do the dense thing and flag it explicitly in the spec under a "Tradeoffs" heading so the user is choosing with eyes open. Never silently ship an accessibility regression.

## Phase 1 — Audit and spec (always do this first)

Produce a written spec before touching any code. The user confirms or redirects, then you rewrite. Work through these passes:

### Pass 1: Inventory data-available vs data-displayed

Read the component and whatever feeds it (the API/DTO/view-model type, the props interface, the bound model). List **every field in scope** and mark each as shown or dropped. Dropped fields that are useful are the first source of free density — they cost zero new data fetching.

Output a table:

| Field | In scope from | Currently shown | Worth showing |
|-------|---------------|-----------------|---------------|
| ... | API response | no | yes — key status |

If you can't see the data source, ask for the DTO/type/API shape. Don't guess at fields.

### Pass 2: Measure the current layout

Characterize what's eating space. Look for the usual offenders:

- **Cards used for tabular data.** N cards each repeating the same labels = the single biggest density leak. Each card re-renders field names that a table states once in a header.
- **One field per row.** Stacked label-above-value when label-beside-value (or a table cell) would do.
- **Decorative whitespace.** Oversized padding, large gaps, section margins, hero spacing that carries no information.
- **Chrome.** Borders, shadows, rounded containers, icons, and dividers that separate things whitespace alone could separate.
- **Single-column on a wide viewport.** Vertical scrolling through content that would fit in 2–3 columns.
- **Truncation that hides data** the user wants, where a wider/denser layout would just show it.

### Pass 3: Pick patterns

Map each problem to the simplest fix. The four core pattern families (full detail in `references/patterns.md`, read it before writing the spec):

1. **Tables over cards + tight rhythm.** Convert repeated-record card lists to a table; state labels once in the header. Compress vertical rhythm (row height, padding, line-height) to the tightest comfortable value.
2. **Multi-column / no needless scroll.** Use the horizontal axis. Multi-column form layouts, side-by-side panels, column-flowed lists. Replace scroll with grid where data is independent.
3. **Inline labels, remove chrome.** Label-beside-value or header-only labels. Strip borders/shadows/containers that don't encode information. Let alignment and whitespace do the grouping.
4. **Progressive disclosure for overflow.** When genuinely too much exists for one view, keep the dense default and tuck secondary data behind expandable rows, hover/detail panels, or a master-detail split — not behind more scrolling.

Prefer the lightest pattern that solves it. A table is simpler than a grid of mini-dashboards. Inline labels are simpler than a disclosure widget. Don't reach for progressive disclosure until the flat dense layout genuinely overflows.

### Pass 4: Write the spec

Emit a spec with these sections:

- **Data audit** — the table from Pass 1.
- **Density leaks** — what's wasting space now, concretely.
- **Proposed layout** — describe the new structure in words (and an ASCII sketch if it helps): what becomes a table, what columns, what goes multi-column, what gets disclosed. Framework-neutral.
- **Pattern rationale** — one line per major change tying it to a pattern.
- **Tradeoffs** — any accessibility or readability cost being accepted for density.
- **Estimated win** — rough before/after on data-displayed and screen area (e.g. "8 fields → 23 fields shown; ~3 cards per screen → ~15 rows per screen").

Then stop and ask the user to confirm or adjust before rewriting.

## Phase 2 — Rewrite (on request)

Once the user approves the spec, produce the actual code. **Now** framework matters — detect it from the files (`.tsx`/JSX → React, `.razor` → Blazor, `.component.ts` + template → Angular, `.vue` → Vue, etc.) and write idiomatic markup for that framework. The CSS layer is mostly portable across all of them; see `references/css.md` for the density CSS toolkit (compact table styles, grid columns, rhythm variables, chrome removal) and `references/frameworks.md` for per-framework markup notes (how to do a table loop in each, where to put styles, gotchas).

Rewrite rules:

- Change structure and styling; **preserve behavior, data bindings, and event handlers.** Don't drop a click handler or a sort because you reflowed into a table.
- Keep it the simplest implementation. No new dependencies, no component libraries unless one is already in use.
- Use semantic structure: real `<table>` for tabular data (not divs), real headers, so the density doesn't cost you semantics.
- Show the before/after and call out exactly which previously-dropped fields are now visible.

## Quick reference

- `references/patterns.md` — the density pattern catalog with before/after examples. Read before writing the spec.
- `references/css.md` — portable CSS for compact tables, grids, rhythm, and chrome removal. Read during Phase 2.
- `references/frameworks.md` — per-framework markup notes (React, Angular, Blazor, Vue, plain HTML). Read during Phase 2 once you know the target.
