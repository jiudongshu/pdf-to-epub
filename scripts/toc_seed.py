#!/usr/bin/env python3
"""Seed toc.json from the OCR of a book's printed table of contents.

    python toc_seed.py ocr/ --pages 11-14 --out toc.json --offset 14

Prints (and writes) a JSON array of candidate entries:

    [{"section": "正编", "title": "论温庭筠词", "author": "叶嘉莹", "page": 50}, ...]

This is a *draft*. OCR of a TOC column mixes title, author and page number in one
line, splits long titles over two lines and drops the dot leaders, so expect to
correct it by hand — but the page numbers are usually right, and they are the
only field the chapter mapper really needs.

"page" stays the PRINTED page number; the offset belongs in spec.json, not here.
"""

import argparse
import json
import os
import re
import sys

FIXES = [("缪钱", "缪钺"), ("缪 钱", "缪钺"), ("冯延已", "冯延巳")]
TAIL_PAGE = re.compile(r"[.．·…/／\s]{0,12}(?P<page>\d{1,4})\s*$")
PAREN = re.compile(r"[（(]\s*(?P<author>[^）)]{1,20})\s*[）)]\s*[.．·…/／\s]{0,8}"
                   r"(?P<page>\d{1,4})\s*$")
AUTHOR_HINT = re.compile(r"(著|撰|编|辑)$")


def load_pages(ocr_dir, first, last):
    out = []
    for page in range(first, last + 1):
        path = os.path.join(ocr_dir, "p%04d.json" % page)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        lines = []
        for x0, y0, x1, y1, text, _score in data["lines"]:
            body = text.strip()
            if not body:
                continue
            if y0 >= 0.88 * max([l[3] for l in data["lines"]] or [1]) and len(body) <= 24:
                continue                      # running head / folio
            lines.append((round(y0, 1), x0, body))
        lines.sort()
        out.append((page, lines))
    return out


def parse_line(text):
    for a, b in FIXES:
        text = text.replace(a, b)
    match = PAREN.search(text)
    if match:
        title = text[: match.start()].strip(" .．·…/／") or text
        return title, match.group("author").strip(), int(match.group("page"))
    match = TAIL_PAGE.search(text)
    if match:
        head = text[: match.start()].strip(" .．·…/／")
        author = ""
        words = head.split()
        if len(words) > 1 and AUTHOR_HINT.search(words[-1]):
            author = words[-1]
            head = " ".join(words[:-1])
        return head or text, author, int(match.group("page"))
    return text, "", None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ocr_dir")
    ap.add_argument("--pages", required=True, help="printed TOC pages, e.g. 11-14 or 11,12")
    ap.add_argument("--out", default="")
    ap.add_argument("--section", default="", help="section label to repeat on every entry")
    ap.add_argument("--forbidden", default="", help="comma separated: never a page number")
    args = ap.parse_args()

    if "-" in args.pages:
        first, last = [int(p) for p in args.pages.split("-", 1)]
    else:
        parts = [int(p) for p in args.pages.split(",")]
        first, last = min(parts), max(parts)

    entries = []
    for page, lines in load_pages(args.ocr_dir, first, last):
        for _y, _x, text in lines:
            title, author, number = parse_line(text)
            if number is None:
                # A wrapped title, or a section head such as 正编 / 续编.
                if entries and (text.startswith(("—", "——", "(")) or len(text) > 8):
                    entries[-1]["title"] += " " + text.strip(" .．·…/／")
                    continue
                print("# p%d  no page number: %s" % (page, text), file=sys.stderr)
                continue
            if entries and (title.startswith(("—", "——", "(")) or not title.strip()):
                # subtitle / "（作者）/123" continuation of the previous entry
                if title.strip():
                    entries[-1]["title"] += " " + title.strip()
                if author:
                    entries[-1]["author"] = author
                entries[-1]["page"] = number
                continue
            if number in set(range(1, 3)) and len(title) < 4:
                continue
            entry = {"section": args.section, "title": title, "author": author,
                     "page": number}
            entries.append(entry)

    print(json.dumps(entries, ensure_ascii=False, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=1)
        print("wrote %s (%d entries)" % (args.out, len(entries)), file=sys.stderr)


if __name__ == "__main__":
    main()
