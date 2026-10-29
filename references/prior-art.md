# Prior art: what other PDF→EPUB converters get right

Notes from reading **deximy/Pdf2Epub** (`Pdf2Epub.Worker/PyWorker/main.py`, ~750 lines,
PyMuPDF + .NET API/Worker + Vue frontend, Docker, MIT). Its README promises layout,
heading and TOC fidelity, batch mode, resumable uploads and history de-duplication.
That is true for *born-digital* PDFs and false for scans, which is the useful lesson.

## The one thing that decides everything: the text layer

`main.py` opens the file with `fitz.open()`, calls `page.get_text("dict")`,
`doc.get_toc()` and `doc.metadata`, and never OCRs anything. Hand it a 307 MB scan of
822 JPEG pages and it produces an empty book. So:

- Route A (text layer) and Route B (scan → OCR → reflow) are **different products**, not
  one pipeline with a flag. Probe first (§1 of `SKILL.md`).
- Never compare "my converter" with "their converter" without saying which input class
  each was built for.

## Taken from it (with the changes it needed)

| Idea | In Pdf2Epub | Here |
|---|---|---|
| End-of-column test to end a paragraph | `is_eoc(line)`: does the line reach its column's right edge? Merge only when `not is_eos(text) and is_eoc(line)` | Same rule, plus a measured tolerance (`--eoc-tol 1.0`) and **per-page** right edges. Measured on one book: continuing lines stop 0.07·cw short (p90 0.21), paragraph-final lines 4.4·cw (p90 26). Pooling the right edge across a whole book inflates it by a character and splits every line. |
| Column detection by coverage histogram | `column_lrs()`: coverage histogram over x, threshold 30% of the peak, gaps become gutters | `detect_columns()` keeps the idea (histogram + gutters, `--gutter 0.3`, `--min-gutter 12`) |
| Column bounds from line edges | mean ± 1.96σ of the non-sentence-end lines | 10th/90th percentile of the page's own lines; made per-page for the reason above |
| Odd/even page geometry | `line["page"] % 2` selects a geometry set | `learn_geometry()` does the same (`odd`/`even`, falling back to `all` below `--min-geo` samples) |
| CJK join without spaces | `unicodedata.east_asian_width(prev) in ("F","W","A")` | Same test in `join_text()`; it beats matching `[\u4e00-\u9fff]` because fullwidth punctuation and kana are covered |
| Splice split boxes | `splice_horizontal_rawlines()` / `splice_vertical_rawlines()` | `splice_baselines()`: same visual line when vertical centres overlap by >50% and the gap is within a few character widths |
| Sentence-end exceptions | skip URLs (`://`), initialisms (`[-2].isupper()`), Latin abbreviations (`[-3] == "."`) | Not implemented — this book is Chinese prose; keep in mind for mixed-script books |
| Hyphen repair in Latin text | `merge_texts()` + a 1.1 MB pickled English word list | Not adopted: an English word list is dead weight for a CJK book, and `words.xz` costs more than it saves |
| Preserve italics/bold/superscript/colour | `reformat_flags()` → `<span style="font-family;font-style;font-weight;color">` | Not implemented yet. **Known gap**: OCR gives no font information, so emphasis is lost either way; the text-layer route could keep it |
| Vertical typesetting | `--vertical` flag, bbox axes swapped (u,l,d,r) | Not implemented yet; on the list for CJK vertical books (still worth doing: RTL columns are a real layout class) |

## Deliberately not taken

- **One XHTML for the whole book** (`OEBPS/page-00001.html` holding every page). Kills
  chapter navigation, makes every reader re-layout 300 KB at a time, and hides errors.
  One document per chapter instead.
- **Base64 data URIs for images.** Inflates the EPUB by a third and defeats image
  caching. Store JPEGs and reference them.
- **`is_title()` returning `False` unconditionally.** Its "heading support" is really
  "bookmarks only": body text gets no `h1`/`h2` at all. Detect headings from geometry,
  and use bookmarks for chapter boundaries.
- **Zip order from `os.walk`** with `ZIP_DEFLATED` for everything. `mimetype` must be
  the first entry and stored, or EPUBCheck fails the package. Write it explicitly.
- **A fake TOC** (`<div style="line-height:0">` + `margin-left:(level-1)*20px`). Use
  `nav.xhtml` + `toc.ncx`, and keep the printed TOC as a real chapter if the book has one.

## Architectural notes worth stealing for a service, not for a one-off

- Pull-based workers: the worker calls the API for a task and reports back over a hub,
  so workers can live behind NAT; the API stays stateless and is the only DB client.
- Container-per-worker for elastic scaling; the CLI is the same image with a shell.
- Content-addressed history (same PDF in → cached EPUB out) is free once you hash the
  upload; it is the cheapest way to make a public converter affordable.

These matter only if the deliverable is a service. A skill that produces one book should
stay a handful of stdlib scripts with resumable JSON artifacts.
