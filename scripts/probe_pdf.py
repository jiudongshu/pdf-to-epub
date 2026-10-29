#!/usr/bin/env python3
"""Probe a PDF and recommend a conversion route (text / scan+OCR / images).

Pure standard library. Byte-level scan is fast and honest: it reports what the
file literally contains rather than what a library guesses.

    python probe_pdf.py book.pdf --json probe.json
"""

import argparse
import json
import os
import re
import sys

PATTERNS = {
    "page": rb"/Type\s*/Page[^s]",
    "pages_node": rb"/Type\s*/Pages",
    "image": rb"/Subtype\s*/Image",
    "font": rb"/Type\s*/Font",
    "tounicode": rb"/ToUnicode",
    "dctdecode": rb"/DCTDecode",
    "flate": rb"/FlateDecode",
    "jpx": rb"/JPXDecode",
    "ccitt": rb"/CCITTFaxDecode",
    "jb2": rb"/JBIG2Decode",
    "encrypt": rb"/Encrypt",
    "objstm": rb"/ObjStm",
    "linearized": rb"/Linearized",
    "producer": rb"/Producer\s*\(([^)]{0,120})\)",
    "creator": rb"/Creator\s*\(([^)]{0,120})\)",
}


def _dec(raw):
    if raw is None:
        return None
    try:
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


def byte_scan(data):
    out = {}
    for name, pat in PATTERNS.items():
        if name in ("producer", "creator"):
            m = re.search(pat, data)
            out[name] = _dec(m.group(1)).strip() if m else None
        else:
            out[name] = len(re.findall(pat, data))
    return out


def library_scan(path):
    """Optional: per-page text length via pypdf, if installed."""
    try:
        from pypdf import PdfReader
    except Exception:
        return None
    try:
        reader = PdfReader(path)
        lengths = []
        for page in reader.pages[: min(20, len(reader.pages))]:
            try:
                lengths.append(len((page.extract_text() or "").strip()))
            except Exception:
                lengths.append(0)
        return {"pages": len(reader.pages), "text_len_sample": lengths,
                "text_pages_in_sample": sum(1 for n in lengths if n > 20)}
    except Exception as exc:                                    # pragma: no cover
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def verdict(scan, lib):
    pages = scan["page"] or scan["pages_node"]
    if scan["encrypt"]:
        return "encrypted", "remove the password/encryption first (qpdf --decrypt or the original source)."
    has_text = bool(scan["font"]) and bool(scan["tounicode"])
    images_per_page = (scan["image"] / pages) if pages else 0

    if lib and lib.get("text_pages_in_sample", 0) >= 0.6 * max(1, len(lib.get("text_len_sample", [1]))):
        return "text", "born-digital: extract the text layer, then reflow."
    if has_text and images_per_page < 0.5:
        return "text", "born-digital: fonts and ToUnicode maps are present."
    if images_per_page >= 0.8 and not has_text:
        return "scan", "pure scan (%0.2f images/page, no fonts): OCR is mandatory." % images_per_page
    if images_per_page >= 0.2:
        return "mixed", "mixed: OCR the scanned pages, extract text from the rest."
    return "unknown", "neither a text layer nor page images were found; inspect sample pages."


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--json", help="write the full probe report here")
    ap.add_argument("--max-bytes", type=int, default=0,
                    help="only scan the first N bytes (0 = whole file; object streams may hide pages)")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit("no such file: %s" % args.pdf)

    size = os.path.getsize(args.pdf)
    with open(args.pdf, "rb") as fh:
        data = fh.read(args.max_bytes) if args.max_bytes else fh.read()

    scan = byte_scan(data)
    lib = library_scan(args.pdf)
    route, why = verdict(scan, lib)

    pages = (lib or {}).get("pages") or scan["page"] or scan["pages_node"]
    report = {"file": os.path.basename(args.pdf), "bytes": size, "pages": pages,
              "route": route, "reason": why, "scan": scan, "pypdf": lib}

    print("file      %s (%.1f MB)" % (report["file"], size / 1048576.0))
    print("pages     %s" % pages)
    print("objects   page=%d pages_node=%d image=%d font=%d tounicode=%d"
          % (scan["page"], scan["pages_node"], scan["image"], scan["font"], scan["tounicode"]))
    print("filters   dct=%d flate=%d jpx=%d ccitt=%d jbig2=%d objstm=%d"
          % (scan["dctdecode"], scan["flate"], scan["jpx"], scan["ccitt"],
             scan["jb2"], scan["objstm"]))
    print("producer  %s" % (scan["producer"] or "?"))
    if lib:
        print("pypdf     %s" % (lib if "error" in lib else
                                "pages=%d text_pages=%d/%d" %
                                (lib["pages"], lib["text_pages_in_sample"], len(lib["text_len_sample"]))))
    print("ROUTE     %s — %s" % (route.upper(), why))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print("wrote     %s" % args.json)


if __name__ == "__main__":
    main()
