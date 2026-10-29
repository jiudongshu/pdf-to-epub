#!/usr/bin/env python3
"""Extract one image per PDF page, in true page order, without third-party deps.

    python extract_pages.py book.pdf --out pages/            # all pages
    python extract_pages.py book.pdf --out pages/ --limit 20 # smoke test

Handles DCTDecode (JPEG passthrough), FlateDecode images (re-encoded as PNG) and
JPXDecode (written as .jp2 raw). Images are named p0001.jpg, p0002.jpg, ...
and a manifest.json records page -> file, pixel size and media box.
"""

import argparse
import json
import os
import re
import struct
import sys
import zlib

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj")
TYPE_PAGE = re.compile(rb"/Type\s*/Page(?![s\w])")
TYPE_PAGES = re.compile(rb"/Type\s*/Pages")
TYPE_CATALOG = re.compile(rb"/Type\s*/Catalog")
KIDS_RE = re.compile(rb"/Kids\s*\[(.*?)\]", re.S)
REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
XOBJECT_RE = re.compile(rb"/XObject\s*<<(.*?)>>", re.S)
WIDTH_RE = re.compile(rb"/Width\s+(\d+)")
HEIGHT_RE = re.compile(rb"/Height\s+(\d+)")
BPC_RE = re.compile(rb"/BitsPerComponent\s+(\d+)")
CS_RE = re.compile(rb"/ColorSpace\s*/(\w+)")
MEDIABOX_RE = re.compile(rb"/MediaBox\s*\[([^\]]*)\]")
ROTATE_RE = re.compile(rb"/Rotate\s+(-?\d+)")


def parse_objects(data):
    """obj number -> (dict/body bytes, stream bytes or None). Last definition wins."""
    objs = {}
    for m in OBJ_RE.finditer(data):
        num = int(m.group(1))
        start = m.end()
        end = data.find(b"endobj", start)
        body = data[start:end if end != -1 else len(data)]
        stream = None
        s = body.find(b"stream")
        if s != -1:
            head = body[:s]
            p = s + 6
            if body[p:p + 2] == b"\r\n":
                p += 2
            elif body[p:p + 1] in (b"\n", b"\r"):
                p += 1
            e = body.find(b"endstream", p)
            stream = body[p:e if e != -1 else len(body)]
            body = head
        objs[num] = (body, stream)
    return objs


def page_order(objs):
    """Walk the page tree from the catalog. Returns page object numbers in order."""
    catalog = None
    for num, (body, _s) in objs.items():
        if TYPE_CATALOG.search(body):
            catalog = num
            break
    order, seen = [], set()

    def walk(num, depth=0):
        if num in seen or depth > 64:
            return
        seen.add(num)
        body = objs.get(num, (b"", None))[0]
        if TYPE_PAGE.search(body):
            order.append(num)
            return
        kids = KIDS_RE.search(body)
        if not kids:
            return
        for kid in REF_RE.finditer(kids.group(1)):
            walk(int(kid.group(1)), depth + 1)

    if catalog is not None:
        root = REF_RE.search(objs[catalog][0][objs[catalog][0].find(b"/Pages"):][:64])
        if root:
            walk(int(root.group(1)))
    if not order:                                   # no usable tree: object order
        order = sorted(n for n, (b, _s) in objs.items() if TYPE_PAGE.search(b))
    return order


def image_refs(body, objs):
    """Image XObject references directly named in this page object."""
    refs = []
    xo = XOBJECT_RE.search(body)
    if xo:
        refs += [int(m.group(1)) for m in REF_RE.finditer(xo.group(1))]
    if not refs:
        refs = [int(m.group(1)) for m in re.finditer(rb"/Im\w*\s+(\d+)\s+\d+\s+R", body)]
    return [r for r in refs if b"/Subtype" in objs.get(r, (b"", None))[0]]


def png(w, h, ncomp, raw):
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    stride = w * ncomp
    body = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0 if ncomp == 1 else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(body, 6)) + chunk(b"IEND", b""))


def extract(objnum, objs, outdir, page, notes):
    body, stream = objs[objnum]
    if stream is None:
        return None
    w = int(WIDTH_RE.search(body).group(1)) if WIDTH_RE.search(body) else 0
    h = int(HEIGHT_RE.search(body).group(1)) if HEIGHT_RE.search(body) else 0
    bpc = int(BPC_RE.search(body).group(1)) if BPC_RE.search(body) else 8
    cs = (CS_RE.search(body).group(1).decode() if CS_RE.search(body) else "DeviceGray")
    name = "p%04d" % page

    if b"/DCTDecode" in body:
        end = stream.rfind(b"\xff\xd9")             # trim decoder padding after EOI
        data = stream[:end + 2] if end != -1 else stream
        path = os.path.join(outdir, name + ".jpg")
        with open(path, "wb") as fh:
            fh.write(data)
        return {"file": name + ".jpg", "width": w, "height": h, "filter": "DCTDecode"}

    if b"/JPXDecode" in body:
        path = os.path.join(outdir, name + ".jp2")
        with open(path, "wb") as fh:
            fh.write(stream)
        notes.append("page %d is JPEG2000 (.jp2) — not every reader shows it" % page)
        return {"file": name + ".jp2", "width": w, "height": h, "filter": "JPXDecode"}

    if b"/FlateDecode" in body and bpc == 8 and w and h:
        raw = zlib.decompress(stream)
        ncomp = {"DeviceRGB": 3, "DeviceCMYK": 4}.get(cs, 1)
        if len(raw) < w * h * ncomp:
            notes.append("page %d flate image truncated (%d < %d)" % (page, len(raw), w * h * ncomp))
            return None
        path = os.path.join(outdir, name + ".png")
        with open(path, "wb") as fh:
            fh.write(png(w, h, 1 if ncomp == 4 else ncomp, raw if ncomp != 4 else raw))
        if ncomp == 4:
            notes.append("page %d is DeviceCMYK — converted crudely; check the colour" % page)
        return {"file": name + ".png", "width": w, "height": h, "filter": "FlateDecode"}

    notes.append("page %d: unsupported image encoding (%s)" % (page, cs))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="export only the first N pages")
    ap.add_argument("--start", type=int, default=1, help="first page to export (1-based)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(args.pdf, "rb") as fh:
        data = fh.read()
    objs = parse_objects(data)
    order = page_order(objs)
    print("pages in tree: %d, objects parsed: %d" % (len(order), len(objs)))
    if not order:
        sys.exit("no pages found — the PDF may use object streams; use pypdf instead")

    notes, manifest, missing = [], [], []
    all_images = sorted(n for n, (b, _s) in objs.items() if b"/Subtype/Image" in b.replace(b" ", b"")
                        or re.search(rb"/Subtype\s*/Image", b))

    for idx, objnum in enumerate(order, start=1):
        if idx < args.start:
            continue
        if args.limit and idx >= args.start + args.limit:
            break
        body = objs[objnum][0]
        mb = MEDIABOX_RE.search(body)
        entry = {"page": idx,
                 "mediabox": [float(x) for x in mb.group(1).replace(b",", b" ").split()] if mb else None,
                 "rotate": int(ROTATE_RE.search(body).group(1)) if ROTATE_RE.search(body) else 0}
        refs = image_refs(body, objs)
        got = None
        for ref in refs:
            got = extract(ref, objs, args.out, idx, notes)
            if got:
                break
        if not got and all_images:
            missing.append(idx)                     # filled in below, object-order fallback
        entry.update(got or {})
        manifest.append(entry)

    if missing:
        fallback = [n for n in all_images][: len(missing)]
        for entry, objnum in zip([e for e in manifest if "file" not in e], fallback):
            got = extract(objnum, objs, args.out, entry["page"], notes)
            if got:
                entry.update(got)
                notes.append("page %d had no direct image reference; used image object %d"
                             % (entry["page"], objnum))

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    ok = sum(1 for e in manifest if "file" in e)
    print("exported  %d/%d pages -> %s" % (ok, len(manifest), args.out))
    for note in notes[:20]:
        print("note      %s" % note)
    if ok != len(manifest):
        print("WARNING   %d pages have no image; check the route (text pages?)" % (len(manifest) - ok))


if __name__ == "__main__":
    main()
