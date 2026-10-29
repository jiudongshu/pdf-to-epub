# Layout heuristics, and how to retune them

`rebuild_text.py` turns per-page OCR line boxes into paragraphs and headings. The
defaults come from single-column Chinese prose; each one is a flag so a different
book can be retuned without editing code.

Input per line: `[x0, y0, x1, y1, text, score]`, origin top-left, pixels.

## Page geometry

| Quantity | How | Why |
|---|---|---|
| `H` | max `y1` on the page | bottom band for running heads |
| `left` | 10th percentile of `x0` over full lines | text block's left edge |
| `right` | 90th percentile of `x1` over full lines | right edge (justified text) |
| `cw` | `(x1 − x0) / len(text)`, median | one character's width, the unit for all thresholds |
| `hh` | median of `y1 − y0` | one line's height |

Use percentiles, not min/max: one stray box (a page number, a stamp) otherwise
defines the geometry for the whole page.

## Running heads and folios (`--footer-band`, `--footer-repeat`)

A line is a footer template if it sits below `0.88·H` **and** its text with digits
replaced by `#` recurs on at least `--footer-repeat` pages.

- `--footer-repeat 2` is correct. `4` looks safer and drops templates that appear
  once per section — 「目录 1」「目录 2」 occur three times in a 14-page front matter,
  and a leaked folio then becomes a paragraph.
- Normalise before counting: `2灵谿词说正续编`, `17灵谿词说正续编` and `《灵谿词说正续编》
  重版前言 3` are three different templates, which is why digits and surrounding
  punctuation matter more than the exact text.
- Footer lines are dropped, not merged. If a chapter's *last* line lands in the band
  (a page filled to the bottom), narrow the band to `0.9` for that book.

## Paragraph breaks (`--indent`, `--gap`, `--short-end`)

Start a new paragraph when any of these holds:

| Condition | Flag | Typical value |
|---|---|---|
| first-line indent | `x0 − left > --indent·cw` | 1.2 |
| previous line ended early | `prev.x1 < right − --short-end·cw` | 2.0 |
| vertical gap | `y0 − prev.y1 > --gap·hh` | 1.45 |
| previous line ends a sentence **and** the gap is large | — | 0.8·gap·hh |

Joining is language-aware: two Han characters concatenate with no space; anything
else gets one; a trailing `-` absorbs the space (Latin hyphenation).

A book with no first-line indent (block paragraphs separated by blank space) needs
`--indent 99` to disable that rule and `--gap` tuned by eye.

## Headings (`--heading-scale`, `--heading-max`, `--center-tol`, heading min length)

A line is a heading when it is centred (`|centre − page centre| < --center-tol·cw`),
taller than body text (`≥ 1.18·hh`), short (`≤ 40` chars) and does **not** end in
sentence punctuation.

Then drop headings whose normalised length is below 3 characters. Without that
filter, single characters that happen to sit centred — 「三」「懒」「笑」「欹」「倚」 —
become chapter headings and pollute the nav document.

## Image and blank pages (`--image-blocks`, `--image-chars`, `--long-line`)

A page is an image page when it yields at most 6 blocks and no long line, or fewer
than 120 characters with no sentence punctuation. Both conditions must be tightened
together: a real 6-line page in one book (page 245) was classified as an image and
its text silently vanished from the EPUB. When in doubt, favour `text` and let a
caption be wrong; a missing page is worse.

Blank pages: zero characters after footer removal.

## Cross-page paragraphs

Do not end a paragraph at a page boundary. If the last block of a page does not end
in `。！？…”」』）】〉》.!?:;；`, join it with the first block of the next page. This
happens at ~2/3 of all page breaks in a prose book; skipping it produces 500
paragraphs that stop mid-sentence.

## Verse

Detect it before any of the above: a poem line is a line. Keep one printed line per
block element (`kind: "verse"`), keep the caption lines (`（《论词的起源》叶嘉莹）`) as
their own class, and do not join or re-flow. If the user wants different typography,
that is a separate, explicit request — see §7 of SKILL.md.
