# OCR engines for scanned books

The engine choice decides whether the EPUB is worth shipping. Averages and
confidence scores do not: an engine that drops rare glyphs still reports 0.99.

## The one test that matters

1. Find 3 pages: a dense body page, a page with this book's rarest characters
   (author names, place names, technical terms), and the table of contents.
2. OCR them with each candidate.
3. Diff against the page image, character by character, looking for **missing**
   characters — not substitutions.
4. Encode the survivors as `--must-contain 谿,钺,...` so the batch run asserts them
   over all pages.

Observed in practice, on a 2014 Beijing University Press scan:

| Engine | 灵谿词说 | 缪钺全集 | Line confidence | Verdict |
|---|---|---|---|---|
| `rapidocr-onnxruntime` 1.4.4 (PP-OCRv4) | 灵词说 | 缪全集 | 0.99 | **rejected** — rare glyphs emitted as CTC blanks |
| `rapidocr` 3.10.0 (PP-OCRv6 small) | 灵谿词说 | 缪钺全集 | 0.994–1.000 | accepted |

The failure mode is silent: the recogniser skips unlearned glyphs, the line still
scores high because every emitted character is confident, and the loss only shows
up when someone reads the book.

## Install

```text
pip install "rapidocr>=3.10" onnxruntime
```

`rapidocr` ships the ONNX detection/recognition/classification models and picks
`onnxruntime` as the inference backend; CPU is fine. GPU is not worth it for a
serial text book — the pool already saturates the CPU.

Engine in a different interpreter or site directory? No reinstall needed:

```text
python scripts/ocr_pages.py pages/ --out ocr/ --sys-path "C:\runtime\ocr;D:\deps\onnx"
# or: set OCR_SYS_PATH=C:\runtime\ocr;D:\deps\onnx
```

## Throughput

Measured on a 822-page book, 1700×2675 px pages, CPU-only:

| Workers | Wall clock | Per page |
|---|---|---|
| 1 | 3.2 s/page | 44 min |
| 10 | 1.3 s/page | 17.9 min |

Set `intra_op_num_threads=1` and `inter_op_num_threads=1` in the engine params and
do the parallelism with processes. Nested threading oversubscribes the CPU and
loses 30–40%.

Run it as a background job. Every page writes its own `pNNNN.json` and existing
files are skipped, so an interruption costs nothing — re-running resumes.

## Alternatives, and when they are right

| Engine | Reach for it when | Cost |
|---|---|---|
| `rapidocr` (PP-OCR) | default: modern print, Chinese, good speed | none |
| Tesseract `chi_sim` | already installed, Latin-heavy books | weaker on Chinese punctuation and rare glyphs |
| PaddleOCR (full) | you need layout analysis (columns, tables) with the same models | large install |
| Cloud OCR | multi-column magazines, handwriting, high accuracy demanded | money, privacy, size limits |

## When NOT to OCR

- Handwriting, seals, calligraphy reproductions.
- Art books, maps, sheet music: layout *is* the content — the image route is the
  honest answer.
- Pinyin-only or vertically-set classical text where the reading order is not
  top-to-bottom-left-to-right: the reconstruction step will scramble it.

Say so and offer the fixed-layout image EPUB rather than shipping a text book with
garbled column order.

## After OCR

OCR is transcription, not proofreading. Deliver with that sentence, then offer a
targeted pass over the classes of text where errors matter: rare characters, names,
dates, numbers, ISBN, and the table of contents.
