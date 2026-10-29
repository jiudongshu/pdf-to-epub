# EPUB 3 packaging checklist

`build_epub.py` implements this list; it is written down so you can diagnose a
broken package instead of guessing.

## Archive

```
mimetype                  <- FIRST entry, STORED, exactly "application/epub+zip"
META-INF/container.xml
OEBPS/content.opf
OEBPS/nav.xhtml
OEBPS/toc.ncx
OEBPS/css/style.css
OEBPS/text/ch001.xhtml
OEBPS/images/p0001.jpg
```

- `mimetype` first and uncompressed. Python: `zf.writestr(zipfile.ZipInfo("mimetype"),
  "application/epub+zip", zipfile.ZIP_STORED)`, then everything else `ZIP_DEFLATED`.
- No stray `__MACOSX/`, no `.DS_Store`, no thumbnail directory left over from an
  earlier tool.
- Paths are case-sensitive inside the archive even on Windows. `Images/p1.jpg` in
  the OPF and `images/p1.jpg` on disk is a failure readers report as a blank page.

## container.xml

```xml
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
```

## OPF

Required: `version="3.0"`, `unique-identifier="bookid"`, a `dc:identifier` with that
`id`, `dc:title`, `dc:language`, `dcterms:modified` in `YYYY-MM-DDTHH:MM:SSZ`.

```xml
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier id="bookid">urn:isbn:978-7-301-24042-7</dc:identifier>
  <dc:title>灵谿词说正续编</dc:title>
  <dc:creator id="cre1">缪钺</dc:creator>
  <meta refines="#cre1" property="role" scheme="marc:relators">aut</meta>
  <dc:language>zh-CN</dc:language>
  <dc:publisher>北京大学出版社</dc:publisher>
  <dc:date>2014-11</dc:date>
  <meta property="dcterms:modified">2024-01-01T00:00:00Z</meta>
</metadata>
```

- A stable `dc:identifier` matters: readers key bookmarks, reading position and
  annotations on it. Use the ISBN (`urn:isbn:...`) or a name-based UUID
  (`uuid.uuid5(uuid.NAMESPACE_URL, ...)`), never `uuid4()` on every build.
- `dc:date` is the publication date; `dcterms:modified` is the build timestamp and
  must be refreshed on every build.
- Every `itemref` points at a manifest `id`; every manifest `href` exists.
- The nav document: `<item ... properties="nav"/>`.
- Cover: the image item gets `properties="cover-image"` **and** `<meta name="cover"
  content="img0001"/>` for older readers.

## XHTML

```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head><meta charset="utf-8"/><title>论温庭筠词</title>
<link rel="stylesheet" type="text/css" href="../css/style.css"/></head>
```

- Well-formed XML: every tag closed, `&` `>` `<` escaped, no bare `&nbsp;`.
- `xmlns:epub` is required if you use `epub:type` anywhere.
- Relative paths only, and they are relative **to the XHTML file**, so `../images/`.

## Navigation that works

Three separate things, easy to conflate:

- **Reader TOC panel** — the `<nav epub:type="toc">` list in `nav.xhtml`; one `<li>` per
  document, `href` to the XHTML file. Without it the reader shows nothing to click.
- **In-book TOC page** — if you reproduce the printed 「目录」, each line must be a link
  (`<a href="text/ch012.xhtml">论温庭筠词（缪钺）</a> <span class="pg">50</span>`).
  Paragraph-only text looks identical and is dead. Build the chapters first, then insert
  the TOC document, so hrefs (`ch%03d` = index + count of front-matter docs) are known.
- **Page anchors / page-list** — `<span epub:type="pagebreak" role="doc-pagebreak"
  id="pg64" title="50"/>` at each page boundary, plus

  ```xml
  <nav epub:type="page-list" id="page-list" hidden="hidden"><h1>页码</h1>
    <ol><li><a href="text/ch012.xhtml#pg64">50</a></li>…</ol></nav>
  ```

  `title`/link text is the **printed** page number, `id` is derived from the PDF page —
  keep them distinct or citations point at the wrong page. Both forms pass EPUBCheck 5.1
  with zero warnings (810 anchors in one 822-page book). Readers that ignore it lose only
  the "go to page" dialog, so it is always worth adding.

## NCX (legacy readers)

`toc.ncx` with `dtb:uid` matching the OPF identifier, one `navPoint` per chapter,
`playOrder` ascending from 1. The OPF spine references it with `toc="ncx"`.

## Validation

Local, no Java:

```text
python scripts/validate_epub.py book.epub --verbose
```

Structural, plus body-character counts per document — a chapter that collapsed to
0 characters shows up immediately.

Authoritative:

```text
java -jar epubcheck-5.1.0/epubcheck.jar book.epub
```

Target output: `Messages: 0 fatals / 0 errors / 0 warnings / 0 infos`. Warnings are
worth reading: unmanifested resources and `epub:type` misuse are usually warnings
first and reader bugs later.

## Things that pass validation but still look broken

| Symptom | Cause |
|---|---|
| Blank page where an image should be | image not in the manifest, or path case mismatch |
| Two chapters merged in the TOC | duplicate `id` in manifest |
| Reading position resets every open | `dc:identifier` changed between builds |
| Fonts look default everywhere | stylesheet link path wrong, reader silently ignores it |
| Indentation weird in one reader only | that reader injects `p{text-indent:2em}` — see SKILL.md §7 |
