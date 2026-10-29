# Layout heuristics, and how to retune them

`rebuild_text.py` turns per-page OCR line boxes into paragraphs and headings. The
defaults come from single-column Chinese prose; each one is a flag so a different
book can be retuned without editing code.

Input per line: `[x0, y0, x1, y1, text, score]`, origin top-left, pixels.

## Page geometry

| Quantity | How | Why |
|---|---|---|
| `H` | max `y1` on the page | bottom band for running heads |
| `left` | 10th percentile of the page's `x0` | text block's left edge |
| `right` | 90th percentile of the page's `x1` | right edge (justified text) |
| `cw` | `(x1 − x0) / len(text)`, median | one character's width, the unit for all thresholds |
| `hh` | median of `y1 − y0` | one line's height |

Use percentiles, not min/max: one stray box (a page number, a stamp) otherwise
defines the geometry for the whole page.

**Percentiles are per page, not per book.** A book is printed in sections and the
margins move: pooling every line gives one `right` that fits no section, and a `right`
one character too wide makes every line "short", i.e. every line a paragraph (+111%
blocks, 4 369 → 9 204, in the book this was measured on). Pooled geometry is still
worth computing for column detection and for pages too sparse to trust (fewer than 8
lines fall back to it).

## Running heads and folios (`--footer-band`, `--footer-repeat`)

Two independent rules, both learned from the book:

1. **Band rule.** Any short line below `--footer-band · H` (default `0.96`) is a
   running head. Keep the band tight. In one book folios sat at 0.983–0.986·H while
   real footnotes sat at 0.86–0.95·H, so the earlier default of `0.88` deleted every
   footnote in the book.
2. **Template rule.** Text with digits replaced by `#` that occurs below the band is a
   running head only when it appears on **≥30% of pages** (`--footer-repeat` is a floor,
   not the threshold) and is not near the top of a page.

The template rule catches folios that a publisher set inside the text block; the
percentage test is what stops it from eating content. Count-based thresholds fail in
both directions: 「目录 1」 occurs three times and is a footer, while 「1957年写定」 and
「(原载《四川大学学报》1985年第4期)」 occur just as often at the bottom of *article-ending*
pages and are text. A `≥2` count rule removed 40 539 characters (6.4%) from one book,
including the closing date and source line of every essay.

- Normalise before counting: `2灵谿词说正续编` and `17灵谿词说正续编` must collapse to
  `#灵谿词说正续编`; `《灵谿词说正续编》重版前言 3` only collapses if the space is
  stripped too.
- Footer lines are dropped, not merged. If a chapter's *last* line lands in the band
  (a page filled to the bottom), narrow the band until it does not.
- When a line survives as a running head in a verse chapter, compare it after blanking
  digits (`re.sub(r"\d+", "#", text)`), never with `text in template` — the template
  starts with `#` and the page's line starts with `16`.

## Paragraph breaks (`--indent`, `--gap`, `--eoc-tol`)

Start a new paragraph when any of these holds:

| Condition | Flag | Typical value |
|---|---|---|
| previous line stopped short of the right edge | `prev.x1 < right − --eoc-tol·cw` | 1.0 |
| first-line indent | `x0 − left > --indent·cw` | 2.0 |
| vertical gap | `y0 − prev.y1 > --gap·hh` | 1.45 |

The end-of-column test is the one that carries a Chinese book: prose is justified, so a
line that continues the paragraph reaches `right` to within a fraction of a character,
while the last line of a paragraph stops short by several character widths and usually
has **no** full stop (it ends in 「，」「：」「」」 or nothing). Relying on sentence
punctuation alone merges verse lines, list items and heading lines into the paragraph
above them.

Measure the tolerance instead of guessing: take every line that is followed by another
line at normal leading, split by "ends with sentence punctuation or not", and print
percentiles of `(right − x1)/cw`. One book: continuing lines p50 0.07 / p90 0.21,
paragraph-final lines p50 4.4 / p90 26.4 — a two-order-of-magnitude gap, so any
threshold between 0.5 and 3 behaves; 1.0 is safe.

Joining is language-aware: `unicodedata.east_asian_width(prev) in ("F","W","A")` means
no space (Han, fullwidth punctuation, kana, ambiguous-width quotes); anything else gets
one; a trailing `-` absorbs the space (Latin hyphenation).

A book with no first-line indent (block paragraphs separated by blank space) needs
`--indent 99` to disable that rule and `--gap` tuned by eye.

## Headings (`--heading-scale`, `--heading-max`, `--heading-min`, `--center-tol`)

A line is a heading when it is centred (`|centre − page centre| < --center-tol·cw`),
narrower than 60% of the column, taller than body text (`≥ 1.18·hh`), short (`≤ 40`
chars), has at least `--heading-min` (3) non-punctuation characters, and does **not**
end in sentence punctuation.

Each extra condition exists because of a real false positive: centred single glyphs
(「三」「懒」「笑」「欹」「倚」) became chapter headings; centred lyric lines of a poem
inside a prose chapter became section headings; a centred *separator* became a chapter
of its own. The 60%-width test is what separates a title from a centred verse line,
because a verse line is as wide as its column.

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
