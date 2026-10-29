#!/usr/bin/env python3
"""Read the PDF's own outline (bookmarks) and turn it into a chapter map.

    python outline_toc.py book.pdf --out toc.json
    python outline_toc.py book.pdf            # just report what is there

Why this matters: an outline gives exact chapter boundaries for free -- no OCR of
the printed table of contents, no printed-page -> PDF-page offset guessing, no
fuzzy title matching. Always check for an outline before you invest in the
OCR-TOC route (probe_pdf.py reports it as `outline`).

Output entries:

    {"title": "论温庭筠词", "author": "", "pdf_page": 50, "level": 1}

`pdf_page` is already a PDF page number, so build_epub.py uses it directly
(no offset, no searching) -- see the "pdf_page" branch in build_epub.py.

Pure standard library; page order and object parsing are shared with
extract_pages.py.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_pages import parse_objects, page_order          # noqa: E402

NUM_REF = re.compile(rb"(\d+)\s+(\d+)\s+R")


# ---------------------------------------------------------------- PDF values

def _skip(buf, pos):
    while pos < len(buf):
        c = buf[pos:pos + 1]
        if c in b" \t\r\n\f\x00":
            pos += 1
        elif c == b"%":
            while pos < len(buf) and buf[pos:pos + 1] not in b"\r\n":
                pos += 1
        else:
            break
    return pos


def _literal_string(buf, pos):
    """Parse a (...) string; returns (bytes, pos)."""
    assert buf[pos:pos + 1] == b"("
    pos += 1
    out = bytearray()
    depth = 1
    while pos < len(buf):
        c = buf[pos:pos + 1]
        if c == b"\\":
            nxt = buf[pos + 1:pos + 2]
            mapping = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                       b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
            if nxt in mapping:
                out += mapping[nxt]
                pos += 2
            elif nxt.isdigit():                      # \ddd octal
                m = re.match(rb"[0-7]{1,3}", buf[pos + 1:pos + 4])
                out.append(int(m.group(0), 8) & 0xFF)
                pos += 1 + len(m.group(0))
            elif nxt in (b"\r", b"\n"):              # line continuation
                pos += 2
            else:
                out += nxt
                pos += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return bytes(out), pos + 1
        out += c
        pos += 1
    return bytes(out), pos


def decode_text(raw):
    """PDF string bytes -> str (UTF-16BE with BOM, else PDFDocEncoding-ish)."""
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be", "replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def parse_value(buf, pos):
    """Parse one PDF value: dict / array / name / string / number / ref."""
    pos = _skip(buf, pos)
    if pos >= len(buf):
        return None, pos
    two = buf[pos:pos + 2]
    if two == b"<<":
        pos += 2
        d = {}
        while True:
            pos = _skip(buf, pos)
            if buf[pos:pos + 2] == b">>" or pos >= len(buf):
                return d, pos + 2
            if buf[pos:pos + 1] != b"/":
                return d, pos + 1
            m = re.match(rb"/([^\s/<>\[\]()]+)", buf[pos:])
            if not m:
                return d, pos + 2
            key = m.group(1).decode("latin-1")
            pos += m.end()
            val, pos = parse_value(buf, pos)
            d[key] = val
    if buf[pos:pos + 1] == b"[":
        pos += 1
        arr = []
        while True:
            pos = _skip(buf, pos)
            if pos >= len(buf) or buf[pos:pos + 1] == b"]":
                return arr, pos + 1
            val, pos = parse_value(buf, pos)
            arr.append(val)
    if buf[pos:pos + 1] == b"(":
        raw, pos = _literal_string(buf, pos)
        return ("str", decode_text(raw)), pos
    if buf[pos:pos + 1] == b"<":
        m = re.match(rb"<([0-9A-Fa-f\s]*)>", buf[pos:])
        if m:
            hexs = re.sub(rb"\s+", b"", m.group(1))
            if len(hexs) % 2:
                hexs += b"0"
            return ("str", decode_text(bytes.fromhex(hexs.decode()))), pos + m.end()
    m = NUM_REF.match(buf, pos)
    if m:
        return ("ref", int(m.group(1))), m.end()
    if buf[pos:pos + 1] == b"/":
        m = re.match(rb"/([^\s/<>\[\]()]+)", buf[pos:])
        if m:
            return ("name", m.group(1).decode("latin-1")), pos + m.end()
    m = re.match(rb"[+-]?\d*\.?\d+", buf[pos:])
    if m:
        txt = m.group(0)
        return (float(txt) if b"." in txt else int(txt)), pos + m.end()
    return None, pos + 1


# ------------------------------------------------------------ outline walk

def outline_root(objs):
    """The /Outlines dictionary itself (not its first item), or None."""
    for _num, (body, _s) in objs.items():
        if re.search(rb"/Type\s*/Catalog", body):
            m = re.search(rb"/Outlines\s+" + NUM_REF.pattern, body)
            if m:
                return resolve(objs, ("ref", int(m.group(1))))
            m = re.search(rb"/Outlines\s*<<", body)      # rarely inline
            if m:
                return parse_value(body, m.end() - 2)[0]
            return None
    return None


def walk_outline(objs, root, page_index, max_items=5000):
    """Depth-first walk of /First and /Next; returns flat entries with levels."""
    entries = []
    seen = set()

    def item(num, level):
        if num in seen or num not in objs or len(entries) >= max_items:
            return
        seen.add(num)
        body = objs[num][0]
        start = body.find(b"<<")
        node, _ = parse_value(body, start) if start != -1 else ({}, 0)
        if not isinstance(node, dict):
            return
        title = node.get("Title")
        if isinstance(title, tuple) and title[0] == "str":
            title = title[1]
        else:
            title = "(untitled)"
        entries.append({"title": " ".join(str(title).split()),
                        "pdf_page": dest_page(objs, node, page_index),
                        "level": level})
        first = node.get("First")
        nxt = node.get("Next")
        if isinstance(first, tuple) and first[0] == "ref":
            item(first[1], level + 1)
        if isinstance(nxt, tuple) and nxt[0] == "ref":
            item(nxt[1], level)

    if isinstance(root, dict):
        first = root.get("First")
        if isinstance(first, tuple) and first[0] == "ref":
            item(first[1], 1)
    return entries


def resolve(objs, value, depth=0):
    """Follow indirect references to a concrete value."""
    while isinstance(value, tuple) and value[0] == "ref" and depth < 8:
        body = objs.get(value[1], (b"", None))[0]
        start = body.find(b"<<")
        if start == -1:
            m = re.match(rb"\s*\[", body)
            if m:
                return parse_value(body, m.end() - 1)[0]
            return None
        value = parse_value(body, start)[0]
        depth += 1
        if isinstance(value, dict):
            return value
    return value


def named_dest(objs, catalog_body, name):
    """Look a /Name destination up in /Dests or the /Names /Dests name tree."""
    m = re.search(rb"/Dests\s+" + NUM_REF.pattern, catalog_body)
    if m:
        dests = resolve(objs, ("ref", int(m.group(1))))
        if isinstance(dests, dict) and name in dests:
            return dests[name]
    m = re.search(rb"/Dests\s*<<", catalog_body)
    if m:
        dests, _ = parse_value(catalog_body, m.end() - 2)
        if isinstance(dests, dict) and name in dests:
            return dests[name]
    m = re.search(rb"/Names\s+" + NUM_REF.pattern, catalog_body)
    if m:
        tree = resolve(objs, ("ref", int(m.group(1))))
        return tree_lookup(objs, tree, name)
    return None


def tree_lookup(objs, node, name, depth=0):
    if not isinstance(node, dict) or depth > 8:
        return None
    names = node.get("Names")
    if isinstance(names, list):
        for i in range(0, len(names) - 1, 2):
            key, val = names[i], names[i + 1]
            if isinstance(key, tuple) and key[0] == "str" and key[1] == name:
                return val
    kids = node.get("Kids")
    if isinstance(kids, list):
        for kid in kids:
            got = tree_lookup(objs, resolve(objs, kid), name, depth + 1)
            if got is not None:
                return got
    return None


def dest_page(objs, node, page_index):
    """Page number (1-based) for an outline item, or None."""
    dest = node.get("Dest")
    if dest is None:
        action = node.get("A")
        action = resolve(objs, action) if action is not None else None
        if isinstance(action, dict):
            dest = action.get("D")
    if dest is None:
        return None
    dest = resolve(objs, dest)
    if isinstance(dest, tuple) and dest[0] in ("name", "str"):   # named destination
        cat = next((b for _n, (b, _s) in objs.items()
                    if re.search(rb"/Type\s*/Catalog", b)), b"")
        dest = resolve(objs, named_dest(objs, cat, dest[1]))
    if not isinstance(dest, list) or not dest:
        return None
    head = dest[0]
    if isinstance(head, tuple) and head[0] == "ref":
        return page_index.get(head[1])
    if isinstance(head, int):                       # rare: 1-based page number
        return head
    return None


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", help="write toc.json here (build_epub.py can consume it)")
    ap.add_argument("--page-map", help="write {printed page: pdf page} here (scanners with "
                                       "one bookmark per page, named after the printed number)")
    ap.add_argument("--sections", help="write the named (non-numeric) bookmarks here")
    ap.add_argument("--max-level", type=int, default=3, help="keep entries down to this level")
    ap.add_argument("--set", action="append", default=[],
                    help="section label for a level-1 entry, e.g. --set 正编 --set 续编")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit("no such file: %s" % args.pdf)
    with open(args.pdf, "rb") as fh:
        data = fh.read()
    objs = parse_objects(data)
    order = page_order(objs)
    page_index = {num: i for i, num in enumerate(order, start=1)}
    if not order:
        sys.exit("no pages found — the PDF may use object streams; try pypdf instead")

    root = outline_root(objs)
    if root is None:
        print("outline   none — this PDF ships no bookmarks; fall back to the "
              "printed TOC (toc_seed.py) or one chapter per N pages")
        return
    entries = [e for e in walk_outline(objs, root, page_index)
               if e["level"] <= args.max_level]
    if not entries:
        print("outline   /Outlines exists but no items were readable")
        return

    missing = [e for e in entries if not e["pdf_page"]]
    for e in entries:
        e.setdefault("author", "")
        if not e["pdf_page"]:
            e.pop("pdf_page")
    print("outline   %d entries (%d without a resolvable page), depth %d"
          % (len(entries), len(missing), max(e["level"] for e in entries)))
    for e in entries[:12]:
        print("  %s%s  -> p%s" % ("  " * (e["level"] - 1), e["title"][:48],
                                  e.get("pdf_page", "?")))
    if len(entries) > 12:
        print("  ... %d more" % (len(entries) - 12))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=1)
        print("wrote     %s (%d entries)" % (args.out, len(entries)))

    # Scanners made by tools such as FreePic2Pdf ship one bookmark per page whose
    # title *is* the printed page number -- that is a free, exact page map.
    numeric = [(int(e["title"]), e["pdf_page"]) for e in entries
               if re.fullmatch(r"\d+", e["title"]) and e.get("pdf_page")]
    sections = [{"title": e["title"], "pdf_page": e.get("pdf_page")}
                for e in entries if not re.fullmatch(r"\d+", e["title"])]
    if numeric:
        offsets = {}
        for printed, pdf in numeric:
            offsets[printed - pdf] = offsets.get(printed - pdf, 0) + 1
        common = max(offsets, key=offsets.get)
        consistent = offsets[common]
        print("page map  %d numeric bookmarks, %d%% with offset %+d (printed - pdf)"
              % (len(numeric), round(100.0 * consistent / len(numeric)), common))
        if args.page_map:
            mapping = {str(printed): pdf for printed, pdf in numeric}
            with open(args.page_map, "w", encoding="utf-8") as fh:
                json.dump(mapping, fh, ensure_ascii=False, indent=1)
            print("wrote     %s (%d printed pages -> pdf pages); add \"page_map\": "
                  "\"%s\" to spec.json and drop offset/window guessing"
                  % (args.page_map, len(mapping), os.path.basename(args.page_map)))
    elif args.page_map:
        print("page map  no numeric bookmarks — keep using offset + window in spec.json")
    if sections:
        print("sections  %d named bookmarks: %s" % (len(sections), ", ".join(
            "%s@p%s" % (s["title"], s["pdf_page"]) for s in sections[:8])))
        if args.sections:
            with open(args.sections, "w", encoding="utf-8") as fh:
                json.dump(sections, fh, ensure_ascii=False, indent=1)
            print("wrote     %s" % args.sections)
    if missing:
        print("note      %d entries have no destination; their titles are still "
              "useful as a checklist" % len(missing))


if __name__ == "__main__":
    main()
