# Density CSS Toolkit

Portable CSS that works across React, Angular, Blazor, Vue, and plain HTML. The styling layer is where most of the density actually happens, and it barely changes between frameworks — only *where you put it* does (see frameworks.md).

## Rhythm variables

Centralize the spacing scale so density is one place to tune. These are deliberately tight.

```css
:root {
  --row-pad-y: 5px;      /* vertical cell/row padding — the big lever */
  --row-pad-x: 10px;
  --rhythm: 1.3;         /* line-height */
  --gap: 8px;            /* grid/flex gap, was probably 16-24 */
  --rule: 1px solid #e3e3e3;
  --font-data: 13px;     /* data tables read fine at 13px */
}
```

## Compact table

The workhorse. Cuts the generous default padding tables ship with.

```css
.dense-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-data);
  line-height: var(--rhythm);
}
.dense-table th,
.dense-table td {
  padding: var(--row-pad-y) var(--row-pad-x);
  text-align: left;
  white-space: nowrap;        /* drop for cells that should wrap */
}
.dense-table thead th {
  position: sticky;            /* header stays while body scrolls */
  top: 0;
  background: #fff;
  border-bottom: var(--rule);
  font-weight: 600;
}
.dense-table tbody tr + tr td {
  border-top: var(--rule);     /* single thin rule, no zebra, no per-cell borders */
}
.dense-table td.num,
.dense-table th.num { text-align: right; font-variant-numeric: tabular-nums; }

/* row hover only if rows are interactive */
.dense-table tbody tr.clickable:hover { background: #f5f7fa; cursor: pointer; }
```

## Multi-column layout

Grid for forms/panels:

```css
.dense-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--gap);
  align-items: start;
}
```

Column-flow for short independent items (tags, key-values, nav):

```css
.dense-columns {
  columns: 4 180px;   /* 4 cols, min 180px each, collapses responsively */
  column-gap: var(--gap);
}
.dense-columns > * { break-inside: avoid; }
```

Master-detail split:

```css
.dense-split { display: grid; grid-template-columns: 2fr 3fr; gap: var(--gap); }
```

## Inline labels (when not in a table)

```css
.dense-field { display: flex; gap: 6px; align-items: baseline; }
.dense-field > .label { color: #666; font-size: 12px; flex: 0 0 auto; }
.dense-field > .value { font-weight: 500; }
```

Or pack many into a definition grid:

```css
.dense-dl {
  display: grid;
  grid-template-columns: max-content 1fr;  /* label col sized to content */
  gap: 3px 10px;
  font-size: var(--font-data);
}
.dense-dl dt { color: #666; }
.dense-dl dd { margin: 0; font-weight: 500; }
```

## Chrome removal

```css
/* flatten nested card/panel/section containers to nothing */
.dense .card, .dense .panel { border: 0; box-shadow: none; border-radius: 0; padding: 0; background: none; }

/* sections separated by whitespace + one rule, not boxes */
.dense-section + .dense-section { margin-top: 12px; padding-top: 12px; border-top: var(--rule); }
```

## Progressive disclosure (CSS-only expandable row)

```css
.detail-row { display: none; }
tr.expanded + .detail-row { display: table-row; }
.detail-row td { background: #fafbfc; padding: var(--row-pad-y) var(--row-pad-x); }
```

## Accessibility floor (keep even when going dense)

```css
:focus-visible { outline: 2px solid #2563eb; outline-offset: 1px; }  /* never remove */
/* if a tap target goes below 44px for density, that's the tradeoff to flag — but keep focus + contrast */
```

Notes:
- 13px data + #666 labels on white stays above WCAG AA contrast. Don't go lighter than #767676 on white.
- `white-space: nowrap` on table cells maximizes density but can cause horizontal scroll — fine for data grids, flag it for narrow viewports.
