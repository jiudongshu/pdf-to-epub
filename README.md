# dsh-pdf-to-epub

Turn a PDF into a validated, reflowable, searchable **EPUB 3** — including the hard
case: a pure scanned book with no text layer at all.

This is a [DeepSeek Harness](https://github.com/deepseek-ai) skill, and it also works
as a plain Python toolbox: every script is standard library only (except the OCR
engine) and runs from the command line.

```text
pdf ──probe──> has text layer? ──yes──> extract text ─┐
                    │ no                               ├─> rebuild paragraphs
                    └──> page images ──> OCR ──────────┘        and headings
                                                                      │
   printed TOC ──> chapter map ───────────────────────────────────────┤
                                                                      v
                                              EPUB 3 ──> EPUBCheck / validate
```

## What it actually does

| Stage | Script | Output |
|---|---|---|
| Route decision | `scripts/probe_pdf.py` | byte-level report: pages, images, fonts, `/ToUnicode`, filters, encryption, and a `ROUTE` verdict |
| Page images | `scripts/extract_pages.py` | `pages/p0001.jpg…` + `manifest.json`, true page order, no third-party PDF library |
| OCR | `scripts/ocr_pages.py` | `ocr/pNNNN.json`, one file per page, resumable, parallel |
| Layout | `scripts/rebuild_text.py` | `blocks.json`: paragraphs, headings, image pages |
| Chapter list | `scripts/toc_seed.py` | draft `toc.json` from the OCR of the printed contents |
| Page map | `scripts/outline_toc.py` | exact `printed -> PDF` page map from the PDF's own bookmarks (many scans bookmark every printed page) |
| Package | `scripts/build_epub.py` | `book.epub` (EPUB 3, nav + NCX, metadata, cover) |
| Validate | `scripts/validate_epub.py` | structural PASS/FAIL in one second |
| All of the above | `scripts/pipeline.py` | runs the mechanical stages, prints the human ones |

## Install as a DSH skill

```bash
git clone https://github.com/jiudongshu/dsh-pdf-to-epub.git
cp -r dsh-pdf-to-epub ~/.dsh/skills/dsh-pdf-to-epub          # user-scoped: every session sees it
# or: cp -r dsh-pdf-to-epub <project>/.dsh/skills/dsh-pdf-to-epub    # project-scoped
```

The skill is a directory bundle: the directory name and the frontmatter `name` must both
be `dsh-pdf-to-epub`, the entry point is `SKILL.md` (YAML frontmatter `name` +
`description`, then the procedure). Resources live in `scripts/` and `references/`, and
once the directory is inside a skills root it shows up in the skill catalog without any
registration step.

## Quick start

```bash
# 0. dependencies: OCR engine only
pip install "rapidocr>=3.10" onnxruntime

# 1-4. probe, extract, OCR, rebuild  (safe to re-run; completed pages are skipped)
python scripts/pipeline.py book.pdf work/ --workers 8 --must-contain 谿,钺

# 5. exact page map from the PDF's own outline (skip if it has none)
python scripts/outline_toc.py book.pdf --page-map work/page_map.json --out work/toc_outline.json

# 6. chapter list: seed from the printed contents, then correct the titles
python scripts/toc_seed.py work/ocr --pages 11-14 --out work/toc.json

# 6. describe the book (see examples/spec.json), then build
python scripts/build_epub.py work/spec.json --out book.epub

# 7. validate
python scripts/validate_epub.py book.epub --verbose
java -jar epubcheck-5.1.0/epubcheck.jar book.epub     # authoritative
```

Measured end to end on an 822-page scan (452×712 pt, 270 dpi, Chinese):

| Stage | Time |
|---|---|
| probe | 3 min |
| extract 822 JPEGs | 4 min |
| OCR, 10 workers | 17.9 min |
| layout rebuild | 20 min |
| chapter mapping + build | 45 min |
| validation and proofreading | 20 min |

Result: 635,880 characters, 68 documents, 810 page anchors, ~2.3 MB, EPUBCheck
`0 fatals / 0 errors / 0 warnings`. The same book before the layout fixes came out at
633,678 characters: the difference is footnotes, end-of-article notes and a back-cover
price line that the old footer rule was deleting, while the block count stayed flat at
~4,300 — more text, not more fragmentation.

The output is navigable, not just readable: the in-book 「目录」 has one link per chapter,
every page boundary carries an `epub:type="pagebreak"` anchor, and `nav.xhtml` exposes a
`page-list`, so "jump to printed page 457" works in readers that support it.

## The two lessons this repository exists for

1. **Probe before you plan.** Two PDFs of the same size can need completely different
   pipelines. `/Font 0` plus one JPEG per page means there is no text to extract, and
   no amount of `pdfplumber` will change that.
2. **Choose the OCR engine with the book's own rare characters.** PP-OCRv4 read
   「灵谿词说」 as 「灵词说」 while reporting 0.99 confidence per line. Confidence is
   blind to dropped glyphs; `--must-contain` is not.

## Scope and honesty

- OCR is a transcription, not a proofread. Ship it as such, and offer a targeted pass
  over rare characters, names, dates and numbers.
- Single-column prose with a printed table of contents is the sweet spot. Two-column
  academic layouts, vertical classical typesetting, and image-dominant books need the
  fixed-layout route instead.
- Typography in EPUB readers is hostile territory: some readers ignore your stylesheet
  and your inline styles entirely. `SKILL.md` §7 says this plainly.

## Layout

```text
SKILL.md                     the procedure an agent follows
scripts/                     one file per stage, CLI, standard library
references/ocr-engines.md    engine choice, the rare-glyph test, throughput
references/layout-heuristics.md  every threshold, why it is that value
references/epub3-checklist.md    package structure and EPUBCheck
references/troubleshooting.md    locked files, mojibake, empty chapters
references/prior-art.md          other converters: what was taken, what was not
examples/spec.json           a real 62-chapter spec
examples/toc.json            the matching chapter list
```

## License

MIT — see [LICENSE](LICENSE).
