---
name: pptx-builder
description: Build a native, editable PPTX deck (real text, tables and charts) from a render-ready deck spec, with PNG previews and a layout report. Use after the user has approved the scenario and every number has been resolved from facts.
---

# pptx-builder

Turns a **render-ready deck spec** into `deck.pptx`. Each slide is laid out as HTML/CSS in
headless Chromium (so text wraps and shrinks like in a browser), every element is measured, and
python-pptx re-creates it as a native PowerPoint object: text boxes, shapes, real tables and real
charts with their data (Edit Data works). Previews are screenshots of the same layout.

## When to use

After the scenario is approved and resolved: every figure on a slide comes from computed facts.
**Never type a number yourself** — numbers in the spec are copied from facts by the backend.

## Run

```bash
build-deck --spec deck.json --theme theme.json [--logo logo.png] --out out/
build-deck --from-layout out/layout.json --out out/   # re-emit the PPTX only
```

Outputs in `out/`: `deck.pptx`, `slide-NN.png` previews, `layout.json`, `deck.html` and
`report.json`:

```json
{ "pptx": "deck.pptx", "previews": ["slide-01.png"], "issues": [], "timings": {} }
```

Exit code 0 means the deck was written, **even if `issues` is not empty**. Non-zero means the
input was invalid (message on stderr): fix the spec and run again.

## Fixing layout issues

| `kind`     | Meaning                                       | What to do                                    |
|------------|-----------------------------------------------|-----------------------------------------------|
| `shrunk`   | Text was made smaller (down to 75 %) to fit   | Acceptable; shorten the text if it is a title |
| `overflow` | Text still does not fit its box               | Shorten the text or move content to a new slide |
| `outside`  | An element leaves the slide or content area   | Fewer table rows / takeaways on this slide    |

`slide` in an issue is the zero-based slide index; `element` names the CSS class.

## Deck spec

```json
{ "title": "…", "footer": "…", "slides": [ … ] }
```

Text is plain text (no HTML, no Markdown); it is escaped. Russian typography (no-break spaces in
`2 877`, `12,6 %`, `млн ₽`, after short prepositions) is applied automatically.

### `key_takeaways` — opening slide

- `title`, optional `subtitle`
- `kpis`: 3–4 cards `{label, value, unit?, delta?, tone?}`; `value` is short (`"2 877"`, `"+8,2 %"`)
- `takeaways`: 3–4 conclusions, each up to ~2 lines (≈ 160 characters)

### `chart_table` — chart on the left, compact table on the right, optional callout

- `title` (≤ 2 lines), optional `subtitle`, optional `callout` (≤ 2 lines)
- `chart`: `{caption?, categories, series[], valueFormat, secondaryFormat?, secondaryMin?, secondaryMax?}`
  - series: `{name, values, kind: "bar" | "line", axis?: "primary" | "secondary", color, showValues?}`
  - one value per category; only line series may use the secondary axis (combo chart)
  - `valueFormat` is an Excel format code: `#,##0`, `0.0`, `0.0"%"`
- `table`: `{columns: [{header, align, width?}], rows: [[{text, tone?, bold?}]], totalRow?}`
  - up to ~7 rows and 5 columns; only the rows the conclusion needs

`tone` is `positive` | `negative` | `neutral`. `color` is a theme token.

## Theme

`{name, colors: {primary, accent, text, muted, positive, negative, surface, grid, background},
fonts: {heading, body}}`; colors are `#RRGGBB`, fonts are plain family names. Anything else is
rejected (themes come from user files and are written into CSS).

## Adding a slide type

1. `src/templates/<type>.ts` renders the slide; mark elements that become PowerPoint objects with
   `data-pptx="text|shape|image|chart|table"` and optional `data-fit="shrink"`.
2. Add its CSS to `src/document.ts`, its type to `src/types.ts`, and a case in
   `src/templates/index.ts`.

The Python emitter needs no change: it only knows element kinds, not slide types.

## Security

The layout runs with JavaScript disabled by CSP, every network request is aborted, images must be
`data:` URLs, and the Node process receives no secrets from the environment.
