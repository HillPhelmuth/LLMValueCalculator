# Per-Framework Markup Notes

The patterns and CSS are portable. This file covers the framework-specific part of Phase 2: how to write the dense markup idiomatically and where styles live. Detect the framework from the files, then read the matching section.

Detection: `.tsx`/`.jsx` → React · `.razor` → Blazor · `.component.ts` + HTML template or inline template → Angular · `.vue` → Vue · `.html` alone → plain.

The universal move is the same everywhere: **a card-list `v-for`/`@foreach`/`.map()` over a container becomes the same loop over `<tr>` inside one `<table>`.** Only the loop syntax and style-scoping differ.

---

## React

- Card list → table: replace the `.map()` returning `<Card>` with `.map()` returning `<tr key=...>`. Keep the existing `key`, click handlers, and derived values.
- Preserve `onClick`, `onChange`, sort state, memoization. Move the handler from the card to the `<tr>` (or a cell).
- Styles: if CSS Modules (`styles.denseTable`), add classes there; if Tailwind, translate the toolkit to utilities (`px-2.5 py-1 text-[13px]`); if styled-components, one styled `table`. Don't introduce a new styling system the file doesn't already use.
- Conditional columns: `{cond && <td>…</td>}` is fine; keep column count consistent across rows.

```jsx
<table className={styles.denseTable}>
  <thead><tr><th>Name</th><th>Status</th><th className="num">MRR</th></tr></thead>
  <tbody>
    {rows.map(r => (
      <tr key={r.id} className="clickable" onClick={() => onSelect(r)}>
        <td>{r.name}</td><td>{r.status}</td><td className="num">{r.mrr}</td>
      </tr>
    ))}
  </tbody>
</table>
```

---

## Angular

- Card list → table: `*ngFor` (or `@for` in v17+) moves from the card container to `<tr>`. Use `trackBy`/`track` to preserve identity.
- Preserve `(click)`, `[(ngModel)]`, pipes, and any `@Input()`-driven values. Move bindings onto the row/cells.
- Styles: put toolkit CSS in the component's `styleUrls` file (it's scoped by default). For sticky headers across `::ng-deep` boundaries, watch view encapsulation.
- Multi-column forms: reactive-form controls don't care about layout — just wrap the existing `formControlName` inputs in the `.dense-grid`.

```html
<table class="dense-table">
  <thead><tr><th>Name</th><th>Status</th><th class="num">MRR</th></tr></thead>
  <tbody>
    <tr *ngFor="let r of rows; trackBy: trackId" class="clickable" (click)="select(r)">
      <td>{{ r.name }}</td><td>{{ r.status }}</td><td class="num">{{ r.mrr | currency }}</td>
    </tr>
  </tbody>
</table>
```

---

## Blazor

- Card list → table: `@foreach` (or `<Virtualize>` for long lists) emits `<tr>`. Keep `@key`.
- Preserve `@onclick`, `@bind`, and `EventCallback` parameters — move them to the row/cell. Component params stay as-is; you're changing the template, not the `@code` block.
- Styles: scoped CSS in the matching `.razor.css` file is the idiomatic home for the toolkit. It's isolated per component automatically.
- For long grids, `<Virtualize Items="rows" Context="r">` keeps density *and* performance.

```razor
<table class="dense-table">
  <thead><tr><th>Name</th><th>Status</th><th class="num">MRR</th></tr></thead>
  <tbody>
    @foreach (var r in Rows)
    {
      <tr @key="r.Id" class="clickable" @onclick="() => Select(r)">
        <td>@r.Name</td><td>@r.Status</td><td class="num">@r.Mrr.ToString("C")</td>
      </tr>
    }
  </tbody>
</table>
```

(Adam's stack — `.razor.css` scoped styles, `@foreach`/`<Virtualize>`, class-based component params — is the default to assume if Blazor is detected and nothing contradicts it.)

---

## Vue

- Card list → table: `v-for` moves to `<tr>` with `:key`. Keep `@click`, `v-model`, computed values.
- Styles: `<style scoped>` in the SFC is the home for the toolkit.

```vue
<table class="dense-table">
  <thead><tr><th>Name</th><th>Status</th><th class="num">MRR</th></tr></thead>
  <tbody>
    <tr v-for="r in rows" :key="r.id" class="clickable" @click="select(r)">
      <td>{{ r.name }}</td><td>{{ r.status }}</td><td class="num">{{ r.mrr }}</td>
    </tr>
  </tbody>
</table>
```

---

## Plain HTML / templating

- Server loop (Razor Pages, Handlebars, Jinja, ERB, etc.) emits `<tr>` rows. Styles go in the existing stylesheet or a `<style>` block.
- No bindings to preserve beyond `href`/`onclick` already present.

---

## Universal rewrite checklist

- [ ] Real semantic `<table>`/`<thead>`/`<tbody>` for tabular data, not divs.
- [ ] Every event handler / binding / sort that existed still works.
- [ ] Previously-dropped fields now have columns — call out which.
- [ ] Styles placed in the file's existing styling system, no new dependency.
- [ ] Keyboard/tab order and focus rings intact.
- [ ] Show before/after.
