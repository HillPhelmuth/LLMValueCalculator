# Density Pattern Catalog

The four core pattern families, with when-to-use and before/after. Pick the lightest pattern that solves the problem.

## Table of contents

1. Tables over cards + tight rhythm
2. Multi-column / no needless scroll
3. Inline labels, remove chrome
4. Progressive disclosure for overflow
5. Density math: how to estimate the win

---

## 1. Tables over cards + tight rhythm

**The single highest-leverage move.** Any list of records that share a shape is tabular data wearing a card costume. Cards repeat every field label per record; a table states each label once in the header.

**When to use:** more than ~3 records that share the same fields. The more records, the bigger the win.

**Before (cards — ~120px each, labels repeated N times):**

```
┌─────────────────────────┐
│ Name:    Acme Corp      │
│ Status:  Active         │
│ Owner:   J. Smith       │
│ Updated: 2 days ago     │
└─────────────────────────┘
┌─────────────────────────┐
│ Name:    Globex         │
│ Status:  Pending        │
│ ...                     │
```

**After (table — ~32px per row, labels stated once):**

```
Name        Status   Owner      Updated      Region   Tier   MRR
Acme Corp   Active   J. Smith   2d ago       NA       Gold   $4.2k
Globex      Pending  R. Lee     5h ago       EU       Silver $1.1k
```

Note the table also has *room for the dropped fields* (Region, Tier, MRR) that the card had no space for. You raise data-displayed and lower pixels-per-datum in one move.

**Tight rhythm** is the multiplier: once it's a table, compress row height. Default framework/table styles ship with generous padding. Cut vertical padding to 4–6px, set line-height ~1.3, remove zebra striping in favor of a thin rule or nothing. Each row saved is multiplied across every record.

**Watch:** keep one alignment convention (numbers right, text left), keep the header sticky if the table scrolls, and don't kill row hover affordance if rows are clickable.

---

## 2. Multi-column / no needless scroll

Most UIs use only the vertical axis and waste the horizontal one, especially on desktop. If data items are independent, lay them out across columns instead of down a single track.

**When to use:** wide viewport + content that's currently a single vertical column + items that don't depend on reading order.

### Forms
Stacked single-column forms are slow and tall. Group into 2–3 columns. Related fields side by side (City / State / Zip on one row).

```
Before (tall):          After (compact):
First name              First name      Last name
Last name               Email           Phone
Email                   City    State   Zip
Phone
City
State
Zip
```

### Lists that fit in columns
A list of short items (tags, options, nav, key-values) flowing down one column can flow into 2–4 with CSS `columns` or grid.

### Side-by-side panels
Master-detail, filters-beside-results, summary-beside-chart. Put them next to each other instead of stacking and scrolling between.

**Watch:** reading order for screen readers and keyboard tab order must still make sense; set them explicitly when the visual order diverges from source order.

---

## 3. Inline labels, remove chrome

Two moves that compound.

### Inline labels
Label-above-value doubles every field's height. Move to label-beside-value, or — in a table — to a single header row so the per-cell label disappears entirely.

```
Before:        After:
Status         Status: Active     ← inline
Active

               (or in a table, just the value under a "Status" header)
```

### Remove chrome
Chrome is every pixel that isn't data: borders, drop shadows, rounded card containers, heavy dividers, decorative icons, gradient headers. Most of it exists to separate or group — jobs that alignment and modest whitespace do for free.

- Replace bordered cards with whitespace-grouped sections.
- Replace dividers between every row with a single thin rule under the header, or nothing.
- Drop icons that merely decorate a label they sit next to.
- Collapse nested containers (a card inside a panel inside a section) to one level.

**Watch:** some chrome *does* encode information (a status color, a required-field marker, a focus ring). Keep semantic chrome; cut decorative chrome. Never remove focus indicators.

---

## 4. Progressive disclosure for overflow

Only when a genuinely flat dense layout still doesn't fit. Keep the dense default visible; move *secondary* data one interaction away — never behind more scrolling.

**Patterns, lightest first:**

- **Expandable rows** — table row expands to reveal detail fields on click. Primary fields stay in the row.
- **Hover/detail panel** — hovering or selecting a row populates a fixed detail pane. Master-detail.
- **Tabs/segments within a panel** — when one region has several full views.

**When to use:** the data legitimately exceeds one dense screen *and* splits cleanly into primary (always shown) and secondary (on demand). If everything is primary, disclosure just hides what the user came for — don't.

**Watch:** the default view must be useful on its own. Disclosure is for depth, not for hiding the main answer.

---

## 5. Density math: how to estimate the win

Give the user a concrete before/after.

- **data-displayed:** count fields shown before vs after. ("8 → 23 fields")
- **records per screen:** estimate viewport height / per-record height. Cards at 120px vs rows at 32px in an 800px viewport: ~6 vs ~25.
- **pixels-per-datum:** (visible area) / (data-displayed). Report the direction, not false precision.

Example summary line:
> Cards (120px, 4 fields each) → table rows (32px, 7 fields each): ~6 records/screen → ~22, and 4 → 7 fields each. Roughly 4× more data on screen, fields per record up 75%.
