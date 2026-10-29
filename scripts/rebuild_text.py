#!/usr/bin/env python3
"""Rebuild paragraphs and headings from per-page OCR line boxes.

    python rebuild_text.py ocr/ --out blocks.json

Input : ocr/pNNNN.json with "lines": [[x0,y0,x1,y1,text,score], ...]
Output: blocks.json
    {"pages": [{"page": 9, "kind": "text|image|blank",
                "blocks": [{"type": "h2|p", "text": "..."}]}],
     "stats": {...}, "footers": ["#灵谿词说正续编", ...]}

The heuristics are documented in references/layout-heuristics.md. They are tuned
for single-column prose; every threshold is a flag so a different book can be
retuned without editing the code.
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
SENT_END = "。！？…”」』）】〉》.!?:;；"
DIGITS = re.compile(r"\d+")


def load(ocr_dir):
    pages = []
    for name in sorted(os.listdir(ocr_dir)):
        if not re.match(r"^p\d+\.json$", name):
            continue
        with open(os.path.join(ocr_dir, name), encoding="utf-8") as fh:
            pages.append(json.load(fh))
    pages.sort(key=lambda p: p["page"])
    return pages


def learn_footers(pages, band, min_repeat):
    """Lines in the bottom band whose digit-stripped text repeats are running heads."""
    counts = Counter()
    for page in pages:
        for x0, y0, x1, y1, text, _score in page["lines"]:
            if y1 > 0 and y0 >= band * max(l[3] for l in page["lines"]) and len(text) <= 40:
                counts[DIGITS.sub("#", text)] += 1
    return {t for t, n in counts.items() if n >= min_repeat}


def is_footer(line, footer_set, height, band):
    x0, y0, x1, y1, text, _s = line
    if height and y0 >= band * height and len(text) <= 40:
        return True
    return DIGITS.sub("#", text) in footer_set


def median(values, default=0.0):
    values = [v for v in values if v]
    return statistics.median(values) if values else default


def percentile(values, q, default=0.0):
    values = sorted(v for v in values if v is not None)
    if not values:
        return default
    idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return values[idx]


def join_text(a, b):
    if not a:
        return b
    if not b:
        return a
    if CJK.match(a[-1]) and CJK.match(b[0]):
        return a + b
    if a.endswith("-"):
        return a[:-1] + b
    return a + " " + b


def analyse_page(page, footer_set, args):
    lines = [l for l in page["lines"] if l[4].strip()]
    height = max([l[3] for l in lines], default=0)
    if not lines:
        return {"page": page["page"], "kind": "blank", "blocks": []}

    body = [l for l in lines if not is_footer(l, footer_set, height, args.footer_band)]
    if not body:
        return {"page": page["page"], "kind": "blank", "blocks": []}

    lengths = [len(l[4]) for l in body]
    widths = [(l[2] - l[0]) / max(1, len(l[4])) for l in body]
    cw = median(widths, 12.0)
    left = percentile([l[0] for l in body], 0.1)
    right = percentile([l[2] for l in body], 0.9)
    hh = median([l[3] - l[1] for l in body], 20.0)
    full = [l for l in body if l[2] > right - 1.5 * cw]
    if full:
        left = percentile([l[0] for l in full], 0.1)
        right = percentile([l[2] for l in full], 0.9)

    body.sort(key=lambda l: (round(l[1], 1), l[0]))
    total_chars = sum(lengths)
    long_lines = sum(1 for n in lengths if n >= args.long_line)

    blocks = []
    cur = None
    prev = None
    for line in body:
        x0, y0, x1, y1, text, _score = line
        height_ratio = (y1 - y0) / hh if hh else 1.0
        centred = abs((x0 + x1) / 2 - (left + right) / 2) < args.center_tol * cw
        heading = (centred and height_ratio >= args.heading_scale
                   and len(text) <= args.heading_max and text[-1] not in SENT_END)
        if heading:
            blocks.append({"type": "h2", "text": text})
            cur = None
            prev = None
            continue

        indent = x0 - left > args.indent * cw
        gap = (y0 - prev[3]) if prev else 999
        prev_short = bool(prev) and prev[2] < right - args.short_end * cw
        new_par = (cur is None or indent or gap > args.gap * hh
                   or (prev_short and prev[4][-1] in SENT_END))
        if (cur and len(text) <= args.short_line and text[-1] in SENT_END
                and (y0 - prev[3]) > args.gap * hh * 0.8):
            new_par = True
        if new_par:
            cur = {"type": "p", "text": text}
            blocks.append(cur)
        else:
            cur["text"] = join_text(cur["text"], text)
        prev = line

    kind = "text"
    if not total_chars:
        kind = "blank"
    elif len(blocks) <= args.image_blocks and long_lines == 0:
        kind = "image"
    elif total_chars < args.image_chars and long_lines == 0 and len(blocks) <= args.image_blocks * 3:
        kind = "image"
    return {"page": page["page"], "kind": kind, "blocks": blocks}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ocr_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--footer-band", type=float, default=0.88,
                    help="fraction of page height where running heads live")
    ap.add_argument("--footer-repeat", type=int, default=2,
                    help="a stripped template must occur on this many pages (2, not 4)")
    ap.add_argument("--indent", type=float, default=1.2, help="indent threshold in char widths")
    ap.add_argument("--gap", type=float, default=1.45, help="paragraph gap threshold in line heights")
    ap.add_argument("--short-end", type=float, default=2.0,
                    help="a line ending this many char widths early ends the paragraph")
    ap.add_argument("--heading-scale", type=float, default=1.18,
                    help="heading line height over body line height")
    ap.add_argument("--heading-max", type=int, default=40, help="max heading length")
    ap.add_argument("--center-tol", type=float, default=2.0, help="centring tolerance in char widths")
    ap.add_argument("--long-line", type=int, default=15, help="chars that make a line 'long'")
    ap.add_argument("--short-line", type=int, default=12, help="chars that make a line 'short'")
    ap.add_argument("--image-blocks", type=int, default=6,
                    help="a page with this few blocks and no long line is an image page")
    ap.add_argument("--image-chars", type=int, default=120,
                    help="character count under which a page may be an image page")
    args = ap.parse_args()

    pages = load(args.ocr_dir)
    if not pages:
        sys.exit("no pNNNN.json in %s" % args.ocr_dir)
    footers = learn_footers(pages, args.footer_band, args.footer_repeat)
    out = [analyse_page(p, footers, args) for p in pages]

    kinds = Counter(p["kind"] for p in out)
    chars = sum(len("".join(b["text"] for b in p["blocks"])) for p in out)
    per_page = [len("".join(b["text"] for b in p["blocks"])) for p in out]
    med = median(per_page, 1)
    outliers = [p["page"] for p, n in zip(out, per_page) if n < 0.4 * med and p["kind"] == "text"]

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"pages": out, "footer_templates": sorted(footers),
                   "stats": {"pages": len(out), "chars": chars,
                             "kinds": dict(kinds), "median_chars": med,
                             "low_char_pages": outliers}},
                  fh, ensure_ascii=False, indent=1)

    print("pages     %d  chars %d  median %d/page" % (len(out), chars, med))
    print("kinds     %s" % dict(kinds))
    print("footers   %d templates: %s" % (len(footers), sorted(footers)[:6]))
    if outliers:
        print("outliers  %d text pages under 40%% of the median: %s"
              % (len(outliers), outliers[:20]))
    print("wrote     %s" % args.out)


if __name__ == "__main__":
    main()
