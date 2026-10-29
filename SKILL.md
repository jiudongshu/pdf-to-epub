---
name: pdf-to-epub
description: Convert a PDF into a validated, reflowable EPUB3 — probe whether it has a text layer, OCR the scans when it does not, rebuild paragraphs and headings, map the printed table of contents to chapters, and package a searchable book. Use when a PDF is the input and an EPUB (or any reflowable, searchable ebook) is the deliverable, when a scanned book must become selectable text, or when an EPUB needs structural validation.
---

# PDF → EPUB

This skill turns one PDF into one EPUB3. It covers three routes, but most real
requests are **scanned books**, so the OCR route is written out in full.

Work in the task workspace. Keep every intermediate artifact (page images, per-page
OCR JSON, blocks, chapter spec) — the pipeline is resumable, auditable, and each
stage is re-runnable without redoing OCR.

## 0. Ground rules

- **Probe before planning.** Two PDFs with identical file sizes can need completely
  different pipelines. Never assume a text layer exists.
- **Never silently degrade.** If OCR quality is unacceptable, say so and offer the
  image route; do not ship a text EPUB with dropped characters.
- **Deliver one file with one name.** Build to a temp path, swap at the end. A
  reader holding the previous EPUB produces locked-file chaos (`book(修订2).epub`).
- **Validate before delivering.** A structurally invalid EPUB that opens in one
  reader is not a deliverable.
- Do not edit generated XHTML by hand to fix layout; fix the generator and rebuild.

## 1. Probe (this decides the route)

```text
python scripts/probe_pdf.py book.pdf --json probe.json
```

Read the verdict, not just the numbers:

| Signal | Meaning |
|---|---|
| `/Font` count 0 **and** ≈1 image per page | pure scan — OCR is mandatory |
| images + fonts | mixed: some pages scanned (plates, ads), most typeset |
| many fonts, `/ToUnicode` present | born-digital: try the text route first |
| `/Encrypt` present | decrypt before anything else |
| `/ObjStm` present | objects are compressed — a naive regex object scan will miss them; use a real parser |

Route decision:

| Route | When | Cost driver |
|---|---|---|
| **A. Text layer** | probe says born-digital, and extracted text reads correctly on 5 sample pages | minutes |
| **B. Scan + OCR + reflow** (default for scans) | page images are legible at 100% | OCR time: ~1.3 s/page at 8–10 workers |
| **C. Fixed-layout images** | OCR unusable (handwriting, complex layout, art books) or layout fidelity is the point | minutes, but larger file, no search/reflow |

State the chosen route and why before you start producing artifacts.
`references/prior-art.md` records what was taken (and deliberately not taken) from
existing PDF→EPUB converters — read it before reinventing a layout rule.

## 2. Route B pipeline

Every command is a gate: if it fails, stop and fix rather than continuing.

| # | Step | Command | Gate |
|---|---|---|---|
| 1 | probe | `probe_pdf.py` | route confirmed = scan |
| 2 | page images | `extract_pages.py book.pdf --out pages/` | image count == page count reported by probe |
| 3 | OCR | `ocr_pages.py pages/ --out ocr/ --workers 8 --must-contain 谿,钺` | every page has JSON; `must-contain` hits > 0 |
| 4 | layout | `rebuild_text.py ocr/ --out blocks.json` | no page lost; text pages ≫ image pages |
| 5 | page map | `outline_toc.py book.pdf --page-map page_map.json --out toc_outline.json` | if the PDF has bookmarks, `printed − pdf` is constant for every entry |
| 6 | chapters | `toc_seed.py ocr/ --pages 11-14 --out toc.json`, fix the titles by hand, write `spec.json` (see `examples/spec.json`) | every printed page 1..N belongs to exactly one chapter |
| 7 | package | `build_epub.py spec.json --out book.epub` | `chapters 62 matched / 62`, expected document and image counts |
| 8 | validate | `validate_epub.py book.epub --verbose` | `RESULT: PASS`; EPUBCheck 0 fatals / 0 errors |

`pipeline.py book.pdf work/ --workers 8 --must-contain 谿,钺` runs steps 1–4 and prints
the exact commands for 5–8. Every step reuses existing artifacts, so re-running is free.

**Step 3 is the long pole.** Run it as a background job; it prints progress and ETA
every 25 pages and skips pages that already have JSON, so interruption costs nothing.
Expect 1.2–1.6 s/page wall-clock at 8–10 workers on CPU with 1 thread per worker.

## 3. OCR engine selection (do not skip this)

Pick the engine with **the book's own rare characters**, never with average
confidence.

Real failure to reproduce in your head: PP-OCRv4 read 「灵谿词说」 as 「灵词说」 and
「缪钺全集」 as 「缪全集」 — the rare glyphs 谿 and 钺 were emitted as CTC blanks, and
every affected line still scored 0.99. Confidence is blind to dropped characters.
PP-OCRv6 (`rapidocr>=3.10`) read both correctly at 0.99–1.00.

Procedure:

1. Pick 3 sample pages: a dense body page, a page containing rare/archaic characters,
   and the table of contents page.
2. OCR them with two candidate engines/configurations.
3. Compare against the page image; score by **exact rare glyphs**, punctuation, and
   digit strings (dates, page numbers, ISBN).
4. Then declare `--must-contain` from those characters and let step 3 assert them
   over the whole book.

See `references/ocr-engines.md` for installation, model variants, and threading. If the
engine lives outside the running interpreter, pass `--sys-path "dirA;dirB"` (or set
`OCR_SYS_PATH`) — that is how a bundled runtime, a shared venv, or two site directories
are combined.

## 4. Layout reconstruction

`rebuild_text.py` implements the defaults below; tune with flags when a book fights
them. Read `references/layout-heuristics.md` before changing thresholds.

- **Footer/running head**: two rules, both learned from data. (a) any short line with
  `y0 ≥ 0.96·H` is a running head; (b) a template (digits replaced by `#`) is a running
  head only if it occurs on ≥30% of pages, or above 0.85·H. A count-based threshold
  (`≥2`) is wrong twice over: 「目录 1」 appears three times yet is a footer, while
  「1957年写定」 and 「(原载《…》1985年第4期)」 are *end-of-article notes* that repeat
  just as often and are real text — a `≥2` rule deleted 40 000 characters from one book.
  Keep the band tight (0.96, not 0.88): footnotes live at 0.86–0.95·H.
- **Sparse pages are their own case** (cover, plates, colophon, back cover, the last page
  of an article): the band rule deletes real text there — a price line at 0.969·H went
  missing. On a page with `len(lines) ≤ --sparse-lines` (6) and fewer than 2 prose lines
  (`≥ --sparse-prose`, 25 characters), delete a line only if it (a) matches a learned
  template *as a pattern* (`#` → `\d*`, so the bare 「书名」 also matches `#书名`), or
  (b) is a ≤24-character line ending in 1–4 digits on a page that has real prose («title
  + page number» running head). Blanket "bottom band = footer" loses back-cover prices,
  imprint lines and plate captions; blanket "never" leaks three running heads into text.
- **Body geometry**: `left`/`right` are the 10th/90th percentile of the page's own line
  `x0`/`x1`, `cw` = (x1−x0)/len(text) per line, `hh` = median line height. Compute left
  and right **per page**, not once for the whole book: sections of one book are set with
  different margins, and a pooled `right` one character too wide turns every line into
  its own paragraph (+111% blocks in one book). Pooled geometry is still useful to detect
  columns and to fall back on for sparse pages.
- **New paragraph** when the previous line stopped short of `right` by more than
  `1.0·cw` (end-of-column test), or `x0 − left > 2.0·cw` (indent), or the vertical gap
  exceeds `1.45·hh`. The end-of-column test is the important one: a line that ends a
  paragraph stops ~4 character widths short, a line that continues only ~0.1 — measure
  your book instead of trusting a guess, and let a short line end a paragraph even
  without a full stop.
- **Heading** when centred, narrower than 60% of the column, `height ≥ 1.18·hh`, short,
  no terminal punctuation, and at least 3 non-punctuation characters. The width and
  character tests are what keep centred verse lines and single glyphs (「三」) out of
  the heading stream.
- **Image page** when ≤6 blocks and no long line, or `<120` characters with no
  sentence punctuation. Tighten both conditions: a short real page (245th page of one
  book) was misclassified as an image and its text vanished.
- **Cross-page paragraphs**: if the last block of a page does not end in `。！？…”」』）】》
  .!?:` , join it with the first block of the next page.
- **Poetry / verse**: keep the printed line as one unit (`kind: "verse"`). Do not split
  lines into clauses, do not re-flow them, and do not "centre" them per line unless the
  user asks — see §7. A verse chapter is assembled from raw OCR lines, so it does not
  pass through the normal heading removal: filter the printed title, the byline and the
  running head yourself, or six extra "poems" appear (89 lines for 86 poems in one book).
  Match running heads after blanking digits (`16 灵谿词说正续编` → `#灵谿词说正续编`),
  never by substring.

## 5. Chapters from the printed table of contents

Preferred, in this order:

1. **PDF bookmarks** — `outline_toc.py book.pdf --page-map page_map.json --out toc.json`.
   Many scans carry an outline with one bookmark per printed page whose *title is the
   printed page number* (800+ of them in one book). That yields an exact
   `printed → PDF` map (`printed − pdf` constant), no offset guessing, no title
   matching. `build_epub.py` uses `"page_map"` when the spec has it and falls back to
   `offset + window` otherwise. The named bookmarks (封面/书名/版权/目录) also give the
   front matter for free. Entries that already carry `pdf_page` skip search entirely.
2. **Footer page numbers** — read the printed number out of each page's footer and build
   the same map. Survives unnumbered inserts, needs the footer rule to be right.
3. **Constant offset + windowed title match** — the fallback; see the scoring notes below.

- Seed the file: `toc_seed.py ocr/ --pages 11-14 --out toc.json`. It is a *draft* — OCR
  puts title, byline and page number on one line, wraps long titles, and drops the dot
  leaders. The page numbers are usually right, and they are the only field the mapper
  truly needs; correct the titles by hand.
- Parse TOC pages into `{section, title, author, page}`; fix systematic OCR confusions
  (`缪钱`→`缪钺`, `冯延已`→`冯延巳`).
- Normalise before matching: strip spaces/punctuation, fold variant glyphs
  (`幾→几`, `巳→已`, `谿→溪`, `裏→里`, `於→于`, `祇→只`).
- Score with the **longest common substring** between TOC title and the page head, with
  a bonus when it starts at the head: `max(lcs(title, head), lcs(title, whole) − 2)`.
  A "common prefix ≥5" score silently fails every 4-character title.
- Search a window (±6 pages) and require `score ≥ min(6, len(title))`.
- Every chapter ends one page before the next chapter starts; the last runs to the end.
- **Front matter is explicit, not inferred**: cover, title page, colophon/CIP, plates,
  preface. A TOC entry also named 「前言」 may point *into* the body — do not let the
  front-matter rule swallow it (12 pages disappeared that way once).
- Remove the printed title and byline from the top of each chapter, including the case
  where OCR glued title + byline + first sentence into one block.

## 6. Package an EPUB3

`build_epub.py` writes a compliant package; if you hand-roll one instead:

- `mimetype` must be the **first** entry, **stored** (no compression), content exactly
  `application/epub+zip`.
- `META-INF/container.xml` → the OPF path; OPF with `unique-identifier` + `dc:identifier`,
  `dc:title`, `dc:language`, and `dcterms:modified` (`YYYY-MM-DDTHH:MM:SSZ`).
- EPUB3 `nav.xhtml` with `properties="nav"`, plus `toc.ncx` for older readers.
- **Link the printed table of contents.** A TOC chapter rendered as plain paragraphs is a
  dead page: every entry must be `<a href="text/chNNN.xhtml">title（author）</a>` plus its
  printed number. Build the chapter documents first, then insert the TOC document, so the
  hrefs are known (`ch%03d` index = chapter index + number of front-matter documents).
- **Add real page anchors**: `<span epub:type="pagebreak" role="doc-pagebreak" id="pg29"
  title="15"/>` at every page boundary (map PDF page → printed number, see §5), and a
  `<nav epub:type="page-list" id="page-list" hidden="hidden">` in `nav.xhtml` listing them.
  This gives readers a "go to page" dialog and makes printed citations navigable; both
  forms are EPUBCheck-clean (810 anchors in one book, 0 warnings).
- Cover: `properties="cover-image"` on the manifest item **and**
  `<meta name="cover" content="..."/>`.
- Images referenced with relative paths from each XHTML file.
- Chinese body text: `text-indent: 2em`, `line-height: 1.75`, serif family; paragraph
  margins small enough that the page stays a page.

`references/epub3-checklist.md` is the full list of things EPUBCheck will catch.

## 7. Typography and reader compatibility — the honest part

Readers disagree violently. A stylesheet that works in a phone reader may be discarded
entirely by a desktop one: the book's `<h1>` was centred while `p.author` and the verse
lines fell back to left-aligned with a 2em indent, because the reader ignored
`css/style.css` and the inline `style` attributes on those elements.

Rules that follow from that:

1. **Ask which reader** before doing any styling work, and verify in that reader (a
   screenshot) rather than in a browser you control.
2. Structural markup is reliable; style is not. One printed line per block element
   degrades gracefully everywhere.
3. Changes that alter how the book looks are opt-in, and are made **after** the text is
   signed off — never as a side effect of a metadata fix.
4. Do not chase "centred" text with `!important`, `div` swaps and `width: max-content`
   unless the user explicitly wants it; if you do, verify with a hostile stylesheet
   (`p{text-indent:2em!important}`) in a real renderer first.

## 8. Gates before delivery

1. Page-image count == probe's page count.
2. OCR: one JSON per page; `--must-contain` characters all present.
3. No page with an implausible character count (flag ±40% of the median, look at them).
4. Every page 1..N belongs to a chapter (the printed TOC front matter may legitimately
   own some pages).
5. `validate_epub.py` → `RESULT: PASS`; EPUBCheck 5.x → `0 fatals / 0 errors`.
6. Navigation actually navigates: click two entries in the printed TOC and confirm the
   reader lands on the chapter's first page; check one `page-list` entry against the scan.
7. Spot-check ~10 pages against the scan images (first page of a chapter, a poetry page,
   a page with the rarest characters, the last page).
8. One output file, one clear name, no `(copy)`/`(revised)` siblings left behind.

Report what you did **not** verify: OCR is a transcription, not a proofread — say that
plainly, and offer a targeted pass (rare characters, names, dates, numbers) instead of
promising a clean text.

## 9. When things go wrong

`references/troubleshooting.md` — locked files, mojibake from PowerShell round-trips,
missing CJK encoding declarations, chapters collapsing to empty, `mimetype` ordering,
OCR drops, EPUBCheck error decoder.

Two traps worth remembering even if you read nothing else:

- **Never rewrite a UTF-8 script with PowerShell `Get-Content -Raw | Set-Content`** — it
  reads as GBK on a Chinese Windows and produces irreversible `鐏佃翱` garbage. Use the
  `write`/`edit` tools, or Python with explicit `encoding="utf-8"`.
- **A zero exit code is not a pass.** The batch OCR script exited 1 after printing
  `DONE` because of a trailing print bug, and a build script exited 1 after writing a
  complete, valid EPUB. Verify artifacts, not exit codes.
