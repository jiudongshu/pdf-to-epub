# Troubleshooting

## The build cannot write its output

A reader (Calibre, Apple Books, a phone sync client) holds the previous `.epub`
open and Windows locks the file:

```
[System.IO.IOException] The process cannot access the file ... because it is being used by another process
```

- `build_epub.py` tries `book.epub`, then `book (1).epub`, `book (2).epub`, ...
- The right fix is to ask the user to close the reader and build to one final name.
  Leaving `(修订2)` copies behind is not a deliverable.
- Do not "fix" this by deleting the locked file — it fails too, and it looks like
  data loss.

## Mojibake in a script or a data file

Symptom: `鐏佃翱` / `娴嬭瘯` instead of Chinese.

Cause: `PowerShell (Get-Content -Raw) -replace ... | Set-Content` reads UTF-8 files as
GBK on a Chinese Windows. The damage is irreversible (some byte sequences become
`?`), and a repair attempt fails with
`'gbk' codec can't encode character '\ufeff'`.

- Edit code with the editor tools or Python `open(..., encoding="utf-8")`.
- Console *output* being garbled is harmless: set `PYTHONIOENCODING=utf-8` and
  `chcp 65001`, and trust the file on disk.
- A stray BOM at the start of a JSON file breaks `json.load`; read with
  `encoding="utf-8-sig"` when in doubt.

## Chapters that come out empty

- Title matching failed and the chapter's start page equals its end page minus one.
  `build_epub.py` prints `unmatched chapters (n)` with the expected page and score —
  read it rather than assuming success.
- Front-matter rules ate a body chapter: a printed TOC entry named 「前言」 that points
  into the body is easy to confuse with a preface. Keep front matter **explicit** in
  `spec.json`, list it as a chapter instead.
- Chapters silently collapse when two TOC entries resolve to the same page
  (`论晏幾道词` and `论晏幾道《鹧鸪天》词`): fold variant glyphs (`幾→几`) in the
  comparison key so the second entry does not steal the first one's page.

## OCR dropped characters

`--must-contain` reports `WARNING n required character(s) never appeared`. Do not
ship. Try a newer model (`references/ocr-engines.md`), then re-run only the affected
pages after deleting their JSON.

## OCR is slow, or the machine thrashes

- Parallelism belongs in processes (`mp.Pool`), with `intra_op_num_threads=1`.
- Windows spawn: the worker initialiser must receive its arguments via `initargs`,
  not globals — a `Pool` created with globals fails with
  `AttributeError`/`NameError` in the child.
- One thread per worker at 8–10 workers is the sweet spot on a laptop CPU.

## A page of real text is missing from the EPUB

`rebuild_text.py` classified it as an image page. Check `stats.kinds` and
`low_char_pages` in `blocks.json`; loosen `--image-chars`/`--image-blocks`, rebuild.
Then look at the first paragraph of the chapter — the guard against silent loss is
reading, not validation.

## An internal link or image is "not found" on a device but opens on the PC

Case. `src="../Images/p1.jpg"` and `images/p1.jpg` are the same file on Windows and
different files on Android/iOS. Validate on a case-sensitive path, or just keep every
name lowercase.

## The reader shows a blank title page

`<title>` inside `<head>` empty, or the JPEG is progressive. Some simple EPUB
readers cannot render progressive JPEGs; re-save as baseline.

## Everything validates, but the book "looks wrong"

Typography is hostile territory — see SKILL.md §7. Ask which reader is used, get a
screenshot, and change styling only when the user asked for it. Structural markup
(one printed line per block, real headings, no inline hacks) is the durable answer.
