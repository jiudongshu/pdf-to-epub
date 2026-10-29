#!/usr/bin/env python3
"""Assemble an EPUB 3 from rebuilt blocks, a table of contents and page images.

    python build_epub.py spec.json --out book.epub

spec.json:
    {
      "title": "...", "authors": ["..."], "publisher": "...",
      "date": "2014-11", "isbn": "978-7-301-24042-7", "language": "zh-CN",
      "blocks": "blocks.json",          # from rebuild_text.py
      "ocr_dir": "ocr",                 # optional: needed for verse/toc chapters
      "image_dir": "pages",             # optional: needed for cover/image pages
      "toc": "toc.json",                # optional: chapter list, see below
      "page_map": "page_map.json",      # optional: {"printed": pdf} from outline_toc.py
      "offset": 14,                     # printed page + offset = PDF page
      "window": 6,                      # +- pages searched per chapter
      "front": [                        # fixed pages before the first chapter
        {"kind": "cover",   "pages": [1]},
        {"kind": "image",   "pages": [4, 5, 6, 7], "caption": "书影"},
        {"kind": "text",    "pages": [8, 9, 10], "title": "重版前言",
         "author": "叶嘉莹"}
      ],
      "printed_toc": true               # render toc.json as a chapter
    }

toc.json: [{"section": "正编", "title": "论温庭筠词", "author": "叶嘉莹", "page": 50,
            "kind": "prose|verse|image"}, ...]   # page = printed page number

An entry may instead carry "pdf_page" (what outline_toc.py emits): then page
lookup is exact -- no offset, no searching, no fuzzy title matching.

Everything is standard library only, so it runs on a bare Python install.
"""

import argparse
import difflib
import html
import json
import os
import re
import sys
import unicodedata
import uuid
import zipfile

FOLD = {"幾": "几", "巳": "已", "谿": "溪", "裏": "里", "於": "于", "祇": "只"}
FIXES = [("冯延已", "冯延巳"), ("缪钱", "缪钺"), ("缪鉞", "缪钺")]
SENT_END = "。！？…”」』）】〉》.!?:;；"
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


# --------------------------------------------------------------------------- text


def fix(text):
    for a, b in FIXES:
        text = text.replace(a, b)
    # OCR likes to leave spaces between Han characters; they are never wanted there.
    return re.sub(r"(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])", "", text)


def norm(text):
    text = unicodedata.normalize("NFKC", text)
    text = "".join(FOLD.get(ch, ch) for ch in text)
    return re.sub(r"[^\w\u3400-\u9fff]", "", text)


def lcs(a, b):
    if not a or not b:
        return 0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)).size


def esc(text):
    return html.escape(text, quote=False)


# --------------------------------------------------------------------------- data


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def page_map(blocks):
    return {p["page"]: p for p in blocks["pages"]}


def page_text(pages, page, limit=3):
    entry = pages.get(page) or {}
    texts = [b["text"] for b in entry.get("blocks", [])][:limit]
    return "".join(texts)


def find_start(title, pages, expected, window):
    """Locate the page that starts `title`, scored by longest common substring."""
    t = norm(title)
    if not t:
        return None, 0
    best, best_score = None, 0
    for page in range(max(1, expected - window), expected + window + 1):
        if page not in pages:
            continue
        head = norm(page_text(pages, page, 2))
        full = norm(page_text(pages, page, 6))
        score = max(lcs(t, head), lcs(t, full) - 2)
        if head.startswith(t[: min(len(t), 8)]):
            score += 3
        if score > best_score:
            best, best_score = page, score
    threshold = min(6, len(t))
    if best_score < threshold:
        return None, best_score
    return best, best_score


def ocr_lines(ocr_dir, page):
    path = os.path.join(ocr_dir, "p%04d.json" % page)
    if not os.path.exists(path):
        return []
    return load_json(path)["lines"]


def drop_heading(blocks, title, author):
    """Remove the printed title/author lines that lead a chapter."""
    wanted = {norm(title), norm(author or "")}
    out = list(blocks)
    for _ in range(min(6, len(out))):
        if not out:
            break
        text = out[0]["text"]
        nt = norm(text)
        if not nt:
            out.pop(0)
            continue
        hit = (nt in wanted or any(w and (nt.startswith(w) or w.startswith(nt)) for w in wanted)
               or difflib.SequenceMatcher(None, nt, norm(title)).ratio() > 0.55)
        if not hit:
            break
        out.pop(0)
    return out


def merge_blocks(chunks):
    merged = []
    for block in chunks:
        if merged and merged[-1]["type"] == "p" and block["type"] == "p":
            prev = merged[-1]["text"]
            if prev and prev[-1] not in SENT_END:
                merged[-1] = {"type": "p", "text": prev + block["text"]}
                continue
        merged.append(dict(block))
    return merged


# --------------------------------------------------------------------------- xhtml


def verse_html(lines, footers, title="", author=""):
    """One printed line per paragraph: a poem keeps its own line breaks.

    The heading block of the chapter's first page (printed title, the author
    line, the running head of the following pages) is *not* a poem line, so it
    is filtered out here as well -- the verse chapters take raw OCR lines and
    never go through drop_heading().
    """
    t_norm = norm(title)
    a_norm = [norm(part) for part in re.split(r"[、,，\s]+", author or "") if norm(part)]
    parts = ['<div class="poem">']
    dropped_title = False
    for x0, y0, x1, y1, text, _score in lines:
        text = fix(text.strip())
        # Running heads come back as "16 灵谿词说正续编" while the learned template
        # is "#灵谿词说正续编": compare with the digits blanked out, not by substring.
        bare = re.sub(r"\d+", "#", re.sub(r"\s+", "", text))
        if not text or any(bare == f for f in footers):
            continue
        n = norm(text)
        if t_norm and not dropped_title:
            if n and (t_norm == n or (len(n) >= 4 and t_norm.startswith(n))):
                dropped_title = True
                continue
        if t_norm and n and re.fullmatch(re.escape(t_norm) + r"\d+", n):
            continue                                   # running head + page number
        if a_norm and n and (n in a_norm or n == "".join(a_norm)
                             or (len(n) >= 2 and any(n == a for a in a_norm))):
            continue
        if text.startswith(("(", "（")) and ("》" in text or "〉" in text or len(text) <= 24):
            parts.append('<p class="cap">%s</p>' % esc(text))
        else:
            parts.append('<p class="verse">%s</p>' % esc(text))
    parts.append("</div>")
    return "\n".join(parts)


def toc_html(entries):
    rows = ['<h1>原书目录</h1>']
    section = None
    for entry in entries:
        if entry.get("section") and entry["section"] != section:
            section = entry["section"]
            rows.append('<p class="sechead">%s</p>' % esc(section))
        author = entry.get("author") or ""
        label = "%s（%s）" % (entry["title"], author) if author else entry["title"]
        rows.append('<p class="tocline">%s <span class="pg">%s</span></p>'
                    % (esc(label), entry.get("page", "")))
    return "\n".join(rows)


def image_html(pages, image_dir, caption):
    rows = ['<div class="plate">']
    for page in pages:
        name = "p%04d.jpg" % page
        path = os.path.join(image_dir, name)
        if not os.path.exists(path):
            continue
        rows.append('<div class="pageimg"><img src="../images/%s" alt="page %d"/></div>'
                    % (name, page))
    if caption:
        rows.append('<p class="cap">%s</p>' % esc(caption))
    rows.append("</div>")
    return "\n".join(rows)


XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n'
         '<!DOCTYPE html>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml" '
         'xmlns:epub="http://www.idpf.org/2007/ops" lang="%(lang)s" xml:lang="%(lang)s">\n'
         '<head><meta charset="utf-8"/><title>%(title)s</title>'
         '<link rel="stylesheet" type="text/css" href="../css/style.css"/></head>\n'
         '<body%(type_attr)s>\n%(body)s\n</body>\n</html>\n')

CSS = """body { font-family: serif; line-height: 1.75; margin: 0 1em; }
h1 { font-size: 1.5em; text-align: center; margin: 1.6em 0 1.2em; line-height: 1.4; }
h2 { font-size: 1.15em; margin: 1.6em 0 0.8em; }
p { text-indent: 2em; margin: 0.35em 0; text-align: justify; }
p.author { text-indent: 0; text-align: center; margin: 0 0 2em; font-size: 0.95em; color: #444; }
div.poem { margin: 1em 0 1.5em; }
p.verse { text-indent: 0; text-align: center; margin: 0.15em 0; line-height: 1.85; }
p.cap { text-indent: 0; text-align: center; font-size: 0.85em; color: #666; margin: 0.8em 0 1.8em; }
p.sechead { text-indent: 0; text-align: center; font-weight: bold; margin: 1.8em 0 0.8em; }
p.tocline { text-indent: 0; margin: 0.25em 0; }
p.tocline span.pg { float: right; color: #666; }
div.cover { margin: 0; padding: 0; text-align: center; }
div.cover img { max-width: 100%; max-height: 100%; }
div.pageimg { text-align: center; margin: 0.6em 0; }
div.pageimg img { max-width: 100%; }
"""


def build(spec):
    here = os.path.dirname(os.path.abspath(spec["_path"]))
    base = lambda p: p if os.path.isabs(p) else os.path.join(here, p)

    blocks = load_json(base(spec.get("blocks", "blocks.json")))
    pages = page_map(blocks)
    tot_pages = max(pages) if pages else 0
    toc = load_json(base(spec["toc"])) if spec.get("toc") else []
    ocr_dir = base(spec["ocr_dir"]) if spec.get("ocr_dir") else None
    image_dir = base(spec["image_dir"]) if spec.get("image_dir") else None
    offset = int(spec.get("offset", 0))
    window = int(spec.get("window", 6))
    lang = spec.get("language", "zh-CN")

    footers = set(blocks.get("footer_templates", []))
    chapters = []

    for item in spec.get("front", []):
        chapters.append({"kind": item["kind"], "title": item.get("title", ""),
                         "author": item.get("author", ""), "pages": item["pages"],
                         "image_caption": item.get("caption", "")})

    unmatched = []
    starts = []
    pmap = {}
    if spec.get("page_map"):                       # {"printed": pdf} from outline_toc.py
        pmap = {str(k): int(v) for k, v in load_json(base(spec["page_map"])).items()}
    for entry in toc:
        if entry.get("pdf_page"):                  # already a PDF page (outline route)
            start, score = int(entry["pdf_page"]), 999
        else:
            printed = int(entry.get("page", 1))
            expected = pmap.get(str(printed)) or printed + offset
            start, score = find_start(entry["title"], pages, expected, window)
            if start is None:
                unmatched.append((entry["title"], expected, score))
                start = expected
        starts.append(start)
    for i, entry in enumerate(toc):
        end = (starts[i + 1] - 1) if i + 1 < len(starts) else tot_pages
        chapters.append({"kind": entry.get("kind", "prose"), "title": entry["title"],
                         "author": entry.get("author", ""),
                         "pages": list(range(starts[i], max(starts[i], end) + 1))})

    docs = []
    if spec.get("printed_toc") and toc:
        docs.append(("printedtoc.xhtml",
                     XHTML % {"lang": lang, "title": "原书目录", "type_attr": "",
                              "body": toc_html(toc)},
                     "原书目录"))

    for idx, chapter in enumerate(chapters, start=1):
        name = "ch%03d.xhtml" % idx
        title = chapter["title"]
        kind = chapter["kind"]
        head = []
        if kind != "cover" and title:
            head.append("<h1>%s</h1>" % esc(title))
        if chapter.get("author"):
            author = chapter["author"]
            if kind == "verse" and len(author) > 4:      # 缪钺叶嘉莹 -> 缪钺、叶嘉莹
                author = author[:2] + "、" + author[2:]
            head.append('<p class="author">%s</p>' % esc(author))

        if kind == "cover":
            body = image_html(chapter["pages"], image_dir, "") if image_dir else ""
        elif kind == "image":
            body = head[0] if head else ""
            body += image_html(chapter["pages"], image_dir, chapter.get("image_caption", ""))
        elif kind == "verse" and ocr_dir:
            lines = []
            for page in chapter["pages"]:
                lines += ocr_lines(ocr_dir, page)
            body = "\n".join(head) + "\n" + verse_html(
                lines, footers, title=title, author=chapter.get("author", ""))
        else:
            collected = []
            for page in chapter["pages"]:
                entry = pages.get(page)
                if not entry:
                    continue
                collected += [dict(b) for b in entry["blocks"] if b["type"] in ("p", "h2")]
            collected = [{"type": b["type"], "text": fix(b["text"])} for b in collected]
            collected = merge_blocks(drop_heading(collected, title, chapter.get("author", "")))
            body = "\n".join(head) + "\n" + "\n".join(
                "<%s>%s</%s>" % (b["type"], esc(b["text"]), b["type"]) for b in collected)

        attr = ' epub:type="cover"' if kind == "cover" else ""
        docs.append((name, XHTML % {"lang": lang, "title": esc(title or "封面"),
                                    "type_attr": attr, "body": body}, title))

    return docs, pages, toc, spec, base, lang, unmatched


# --------------------------------------------------------------------------- package


def write_epub(path, docs, toc, spec, base, lang):
    ident = spec.get("isbn")
    identifier = ("urn:isbn:%s" % ident) if ident else (
        "urn:uuid:%s" % uuid.uuid5(uuid.NAMESPACE_URL, "epub:%s:%s"
                                   % (spec.get("title", ""), spec.get("_path", ""))))
    images = []
    for _, body, _t in docs:
        for m in re.finditer(r'src="\.\./images/([^"]+)"', body):
            if m.group(1) not in images:
                images.append(m.group(1))

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                'properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="css/style.css" media-type="text/css"/>']
    spine = []
    for i, (name, _body, _t) in enumerate(docs, start=1):
        item_id = "d%03d" % i
        manifest.append('<item id="%s" href="text/%s" media-type="application/xhtml+xml"/>'
                        % (item_id, name))
        spine.append('<itemref idref="%s"/>' % item_id)
    for i, name in enumerate(images, start=1):
        ext = os.path.splitext(name)[1].lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".jp2": "image/jp2"}.get(ext, "image/jpeg")
        manifest.append('<item id="img%04d" href="images/%s" media-type="%s"/>'
                        % (i, name, mime))

    creators = spec.get("authors") or []
    creator_xml = "".join(
        '<dc:creator id="cre%d">%s</dc:creator>' % (i + 1, esc(a)) for i, a in enumerate(creators))
    refines = "".join('<meta refines="#cre%d" property="role" scheme="marc:relators">aut</meta>'
                      % (i + 1) for i in range(len(creators)))

    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid"
         xml:lang="%(lang)s">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">%(identifier)s</dc:identifier>
    <dc:title>%(title)s</dc:title>
%(creators)s
    <dc:language>%(lang)s</dc:language>
    <dc:publisher>%(publisher)s</dc:publisher>
    <dc:date>%(date)s</dc:date>
%(description)s
    <meta property="dcterms:modified">%(modified)s</meta>
%(refines)s
  </metadata>
  <manifest>
    %(manifest)s
  </manifest>
  <spine toc="ncx">
    %(spine)s
  </spine>
</package>
""" % {"lang": lang, "identifier": esc(identifier), "title": esc(spec.get("title", "")),
       "creators": creator_xml, "publisher": esc(spec.get("publisher", "")),
       "date": esc(spec.get("date", "")),
       "description": ("    <dc:description>%s</dc:description>" % esc(spec["description"])
                       if spec.get("description") else ""),
       "modified": spec.get("modified", "2024-01-01T00:00:00Z"),
       "refines": refines,
       "manifest": "\n    ".join(manifest), "spine": "\n    ".join(spine)}

    nav_items = []
    for name, _body, title in docs:
        nav_items.append('<li><a href="text/%s">%s</a></li>' % (name, esc(title or "封面")))
    nav = ("""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="%s" xml:lang="%s">
<head><meta charset="utf-8"/><title>目录</title>
<link rel="stylesheet" type="text/css" href="css/style.css"/></head>
<body>
<nav epub:type="toc" id="toc"><h1>目录</h1>
<ol>
%s
</ol></nav>
</body>
</html>
""" % (lang, lang, "\n".join(nav_items)))

    points = []
    for i, (name, _body, title) in enumerate(docs, start=1):
        points.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                      '<content src="text/%s"/></navPoint>'
                      % (i, i, esc(title or "封面"), name))
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="1"/>
  <meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
  <docTitle><text>%s</text></docTitle>
  <navMap>
    %s
  </navMap>
</ncx>
""" % (esc(identifier), esc(spec.get("title", "")), "\n    ".join(points))

    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    out = zipfile.ZipFile(path, "w")
    out.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
    out.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
    out.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
    out.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
    out.writestr("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED)
    out.writestr("OEBPS/css/style.css", CSS, zipfile.ZIP_DEFLATED)
    for name, body, _title in docs:
        out.writestr("OEBPS/text/%s" % name, body, zipfile.ZIP_DEFLATED)
    for name in images:
        src = os.path.join(base(spec["image_dir"]), name)
        if os.path.exists(src):
            out.write(src, "OEBPS/images/%s" % name, zipfile.ZIP_DEFLATED)
    out.close()
    return len(docs), len(images), len(ncx), opf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    spec = load_json(args.spec)
    spec["_path"] = os.path.abspath(args.spec)
    docs, pages, toc, spec, base, lang, unmatched = build(spec)
    target = args.out
    for suffix in ("", " (1)", " (2)", " (3)"):
        candidate = target.replace(".epub", "%s.epub" % suffix)
        try:
            handle = open(candidate, "ab")
            handle.close()
            target = candidate
            break
        except PermissionError:
            continue
    n_docs, n_images, _ncx, _opf = write_epub(target, docs, toc, spec, base, lang)

    if unmatched and not args.quiet:
        print("unmatched chapters (%d):" % len(unmatched))
        for title, expected, score in unmatched:
            print("  p%-4d score %d  %s" % (expected, score, title))
    print("epub      %s" % target)
    print("documents %d  images %d  pages %d" % (n_docs, n_images, max(pages) if pages else 0))
    print("chapters  %d matched / %d" % (len(toc) - len(unmatched), len(toc)))


if __name__ == "__main__":
    main()
