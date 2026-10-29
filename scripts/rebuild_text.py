#!/usr/bin/env python3
"""Rebuild paragraphs and headings from per-page OCR line boxes.

    python rebuild_text.py ocr/ --out blocks.json

Input : ocr/pNNNN.json with "lines": [[x0,y0,x1,y1,text,score], ...]
Output: blocks.json
    {"pages": [{"page": 9, "kind": "text|image|blank",
                "blocks": [{"type": "h2|p", "text": "..."}]}],
     "stats": {...}, "footer_templates": [...], "columns": [...]}

Two things decide where a paragraph ends, and both are learned from the whole
book rather than from a single page:

  * geometry -- the left and right edge of every column, computed separately for
    recto and verso pages (mirror margins are real);
  * end-of-column -- a merged line must reach the right edge of its column. A
    line that stops short is the end of a paragraph even when the scanner read
    no full stop there (poems, lists, headings, tables).

The heuristics are documented in references/layout-heuristics.md. They are tuned
for prose; every threshold is a flag so a different book can be retuned without
editing the code.
"""

import argparse
import json
import math
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict

SENT_END = "。！？…”」』）】〉》.!?:;；"
DIGITS = re.compile(r"\d+")


# ------------------------------------------------------------------ reading


def load(ocr_dir):
    pages = []
    for name in sorted(os.listdir(ocr_dir)):
        m = re.match(r"^p(\d+)\.json$", name)
        if not m:
            continue
        with open(os.path.join(ocr_dir, name), encoding="utf-8") as fh:
            obj = json.load(fh)
        # accept foreign OCR dumps too: fall back to the file name for the page
        pages.append({"page": int(obj.get("page") or m.group(1)),
                      "lines": obj.get("lines") or []})
    pages.sort(key=lambda p: p["page"])
    return pages


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
    """Join two OCR line texts: no space inside CJK, space inside Latin."""
    if not a:
        return b
    if not b:
        return a
    tail = a.rstrip()[-1:]
    head = b.lstrip()[:1]
    if not tail or not head:
        return a + b
    # Full/wide/ambiguous characters never take a space after them (CJK, fullwidth
    # punctuation, kana); PDF2ePub taught this trick and it beats a CJK regex.
    if unicodedata.east_asian_width(tail) in ("F", "W", "A"):
        return a.rstrip() + b.lstrip()
    if a.endswith("-"):
        return a[:-1] + b
    return a + " " + b


# ------------------------------------------------------------------ footers


def learn_footers(pages, band, min_repeat):
    """Templates of running heads: bottom-band lines that repeat across pages.

    A running head shows up on most pages of a book; an end-of-article note
    ("1957年写定", "(原载《…》1985年第4期)") repeats a handful of times at the
    bottom of *some* pages and is real text. Requiring a large share of pages is
    what keeps those notes, so the threshold is a percentage, not a count.
    """
    counts = Counter()
    used = 0
    for page in pages:
        lines = [l for l in page["lines"] if l[4].strip()]
        if not lines:
            continue
        used += 1
        height = max(l[3] for l in lines)
        for x0, y0, x1, y1, text, _score in lines:
            if y0 >= band * height and len(text) <= 40:
                counts[DIGITS.sub("#", text)] += 1
    need = max(min_repeat, int(round(0.30 * used)))
    return {t for t, n in counts.items() if n >= need}


_TPL_CACHE = {}


def footer_pattern(tpl):
    """A footer template is a pattern: `#` stands for "some digits, or none".

    The book's own running head is printed as `17灵谿词说正续编` on most pages
    but as bare `灵谿词说正续编` on the contents pages, so an exact-match test
    against the learned template misses it.
    """
    pat = _TPL_CACHE.get(tpl)
    if pat is None:
        pat = re.compile("".join(r"\d*" if ch == "#" else re.escape(ch) for ch in tpl))
        _TPL_CACHE[tpl] = pat
    return pat


def matches_template(text, footer_set):
    t = text.strip()
    return any(footer_pattern(tpl).fullmatch(t) for tpl in footer_set)


def is_footer(line, footer_set, height, band, sparse=False, prose=False):
    x0, y0, x1, y1, text, _s = line
    if sparse:
        # A page holding only a handful of lines is a cover, a plate, a
        # colophon or a chapter-end page. A band line there is content (a
        # caption, a price under a barcode, the publisher's imprint) unless it
        # is recognisably a running head:
        #   * it matches a learned template (`#` may be absent), or
        #   * the page does show prose and the line is a short "title + folio".
        if not height:
            return False
        if y0 >= 0.85 * height and matches_template(text, footer_set):
            return True
        return bool(prose and len(text) <= 24 and re.search(r"\d{1,4}$", text)
                    and y0 >= 0.85 * height)
    if height and y0 >= band * height and len(text) <= 40:
        return True
    # A learned running head may sit above the band in some layouts, but it is
    # never in the middle of a text block, so keep a lower bound on y.
    return (matches_template(text, footer_set) and height
            and y0 >= 0.85 * height)


# --------------------------------------------------------------- line fixing


def splice_baselines(lines, ratio=0.5, max_gap=3.0):
    """Re-join fragments of one visual line that OCR reported as separate boxes.

    Two boxes belong together when their vertical centres nearly coincide and the
    horizontal gap between them is smaller than a few characters. Mixed font sizes
    (a Latin name inside a CJK sentence) are the usual cause.
    """
    if len(lines) < 2:
        return lines
    out = []
    for line in sorted(lines, key=lambda l: (round((l[1] + l[3]) / 2.0), l[0])):
        if out:
            prev = out[-1]
            h_prev = max(1.0, prev[3] - prev[1])
            h_line = max(1.0, line[3] - line[1])
            overlap = min(prev[3], line[3]) - max(prev[1], line[1])
            gap = line[0] - prev[2]
            cw = (prev[2] - prev[0]) / max(1, len(prev[4]))
            if (overlap > ratio * min(h_prev, h_line)
                    and -cw <= gap <= max_gap * cw):
                text = join_text(prev[4].rstrip(), line[4].lstrip())
                out[-1] = [prev[0], min(prev[1], line[1]), max(prev[2], line[2]),
                           max(prev[3], line[3]), text,
                           max(prev[5] or 0, line[5] or 0)]
                continue
        out.append(list(line))
    return out


# ---------------------------------------------------------------- geometry


def detect_columns(recs, thresh=0.3, min_gap=12, min_share=0.15):
    """Column intervals from a horizontal coverage histogram.

    Every page line paints its (left, right) span; x positions covered by fewer
    than `thresh` of the busiest position are gutters. Robust for two-column
    academic layouts and harmless for single-column prose (one interval).
    """
    if not recs:
        return []
    lo = int(min(l for l, _r in recs))
    hi = int(max(r for _l, r in recs)) + 1
    span = hi - lo + 1
    diff = [0] * (span + 1)
    for l, r in recs:
        a = max(lo, int(math.ceil(l))) - lo
        b = min(hi, int(math.floor(r))) - lo
        if b >= a:
            diff[a] += 1
            diff[b + 1] -= 1
    cnt = [0] * span
    run = 0
    for i in range(span):
        run += diff[i]
        cnt[i] = run
    cut = (max(cnt) or 1) * thresh

    gaps = []
    i = 0
    while i < span:
        if cnt[i] < cut:
            j = i
            while j < span and cnt[j] < cut:
                j += 1
            if j - i >= min_gap:
                gaps.append((i, j - 1))
            i = j
        else:
            i += 1
    cols, start = [], 0
    for a, b in gaps:
        if a > start:
            cols.append((start + lo, a - 1 + lo))
        start = b + 1
    if start < span:
        cols.append((start + lo, hi))
    cols = [c for c in cols if c[1] - c[0] >= min_share * span]
    return cols or [(lo, hi)]


def learn_geometry(bodies, args):
    """{(parity): [((col_l, col_r), (text_l, text_r)), ...]} for recto/verso/all."""
    samples = defaultdict(list)
    for page, body in bodies:
        key = "odd" if page % 2 else "even"
        for line in body:
            samples[key].append((line[0], line[2]))
            samples["all"].append((line[0], line[2]))

    geo = {}
    for key in ("odd", "even", "all"):
        recs = samples.get(key, [])
        if len(recs) < max(20, args.min_geo):
            geo[key] = None
            continue
        cols = detect_columns(recs, args.gutter, args.min_gutter)
        bounds = []
        for col_l, col_r in cols:
            pad = 0.05 * (col_r - col_l)
            inside = [(l, r) for l, r in recs
                      if l >= col_l - pad and r <= col_r + pad]
            if len(inside) < 10:
                continue
            bounds.append(((col_l, col_r),
                           (percentile([l for l, _r in inside], 0.10),
                            percentile([r for _l, r in inside], 0.90))))
        if bounds:
            geo[key] = bounds
    for key in ("odd", "even"):
        if not geo.get(key):
            geo[key] = geo.get("all")
    return geo


def column_of(geo, line):
    """The (col, bounds) pair holding this line, or the widest column."""
    l, r = line[0], line[2]
    mid = (l + r) / 2.0
    best = None
    for col, bd in geo:
        col_l, col_r = col
        if col_l - 1 <= mid <= col_r + 1:
            width = col_r - col_l
            if best is None or width > best[0][1] - best[0][0]:
                best = (col, bd)
    if best:
        return best
    return max(geo, key=lambda cb: cb[0][1] - cb[0][0])


# ---------------------------------------------------------------- per page


def page_body(page, footers, args):
    lines = [l for l in page["lines"] if l[4].strip()]
    if not lines:
        return [], 0
    height = max(l[3] for l in lines)
    # A page with a handful of lines is a cover, a plate, a colophon or a
    # chapter-end page. If it does not even hold two prose-length lines, there
    # is no text block for a running head to belong to, and lines in the bottom
    # band are content (a caption, a price under a barcode, an imprint).
    n_prose = sum(1 for l in lines if len(l[4]) >= args.sparse_prose)
    prose = n_prose >= 1
    sparse = len(lines) <= args.sparse_lines and n_prose < 2
    body = [l for l in lines
            if not is_footer(l, footers, height, args.footer_band, sparse, prose)]
    body = splice_baselines(body, args.splice_overlap, args.splice_gap) if args.splice else body
    return body, height


def analyse_page(page, body, geo, args):
    no = page["page"]
    if not body:
        return {"page": no, "kind": "blank", "blocks": []}

    body.sort(key=lambda l: (round(l[1], 1), l[0]))
    lengths = [len(l[4]) for l in body]
    widths = [(l[2] - l[0]) / max(1, len(l[4])) for l in body]
    cw = median(widths, 12.0)
    hh = median([l[3] - l[1] for l in body], 20.0)
    columns = geo["odd" if no % 2 else "even"] or geo.get("all") or []

    # Page-local edges. Pooled geometry drifts between sections of one book
    # (a preface printed with wider margins, a body set tighter), and a right edge
    # one character too wide turns every line into its own paragraph.
    page_left = percentile([l[0] for l in body], 0.10) if len(body) >= 8 else None
    page_right = percentile([l[2] for l in body], 0.90) if len(body) >= 8 else None

    blocks = []
    cur = None
    prev = None
    for line in body:
        x0, y0, x1, y1, text, _score = line
        if columns:
            _col, (left, right) = column_of(columns, line)
        else:                                       # no usable geometry
            left = percentile([l[0] for l in body], 0.1)
            right = percentile([l[2] for l in body], 0.9)
        if page_left is not None:
            left, right = page_left, page_right
        height_ratio = (y1 - y0) / hh if hh else 1.0
        centred = abs((x0 + x1) / 2 - (left + right) / 2) < args.center_tol * cw
        # A heading is short, has real characters in it and does not run the full
        # width of the column. Without the last two tests a centred line of verse
        # ("三", a lyric line) is promoted to a section heading.
        solid = sum(1 for ch in text
                    if not ch.isspace() and not unicodedata.category(ch).startswith("P"))
        narrow = (x1 - x0) < 0.6 * (right - left) if right > left else True
        heading = (centred and narrow and solid >= args.heading_min
                   and height_ratio >= args.heading_scale
                   and len(text) <= args.heading_max and text[-1] not in SENT_END)
        if heading:
            blocks.append({"type": "h2", "text": text})
            cur = None
            prev = None
            continue

        indent = x0 - left > args.indent * cw
        gap = (y0 - prev[3]) if prev else 999
        # End of column: the line reaches the right edge of its own column. A line
        # that stops early ends the paragraph even without sentence punctuation.
        prev_eoc = bool(prev) and (prev[2] >= right - args.eoc_tol * cw)
        prev_eos = bool(prev) and prev[4].rstrip()[-1:] in SENT_END
        prev_short = bool(prev) and prev[2] < right - args.short_end * cw

        new_par = cur is None or indent or gap > args.gap * hh
        if not new_par and not prev_eoc:            # stopped short -> paragraph end
            new_par = True
        if not new_par and prev_short and prev_eos:
            new_par = True
        if (not new_par and cur and len(text) <= args.short_line
                and text[-1] in SENT_END and (y0 - prev[3]) > args.gap * hh * 0.8):
            new_par = True

        if new_par:
            cur = {"type": "p", "text": text}
            blocks.append(cur)
        else:
            cur["text"] = join_text(cur["text"], text)
        prev = line

    kind = "text"
    total_chars = sum(lengths)
    long_lines = sum(1 for n in lengths if n >= args.long_line)
    if not total_chars:
        kind = "blank"
    elif len(blocks) <= args.image_blocks and long_lines == 0:
        kind = "image"
    elif (total_chars < args.image_chars and long_lines == 0
          and len(blocks) <= args.image_blocks * 3):
        kind = "image"
    return {"page": no, "kind": kind, "blocks": blocks}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ocr_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--footer-band", type=float, default=0.96,
                    help="fraction of page height below which any short line is a "
                         "running head; footnotes usually sit above this")
    ap.add_argument("--footer-repeat", type=int, default=2,
                    help="minimum occurrence count for a running-head template")
    ap.add_argument("--sparse-lines", type=int, default=6,
                    help="pages with at most this many lines (covers, plates, "
                         "colophons, chapter-end pages) only lose *learned* "
                         "running heads, never a band-rule line")
    ap.add_argument("--sparse-prose", type=int, default=25,
                    help="a sparse page with a line at least this long still "
                         "counts as a text page and keeps the band rule")
    ap.add_argument("--indent", type=float, default=1.2, help="indent threshold in char widths")
    ap.add_argument("--gap", type=float, default=1.45, help="paragraph gap threshold in line heights")
    ap.add_argument("--eoc-tol", type=float, default=1.0,
                    help="a line within this many char widths of the column edge is 'full'")
    ap.add_argument("--short-end", type=float, default=2.0,
                    help="a line ending this many char widths early ends the paragraph")
    ap.add_argument("--heading-scale", type=float, default=1.18,
                    help="heading line height over body line height")
    ap.add_argument("--heading-max", type=int, default=40, help="max heading length")
    ap.add_argument("--heading-min", type=int, default=3,
                    help="min non-punctuation characters in a heading; a centred "
                         "single glyph is a separator, not a title")
    ap.add_argument("--center-tol", type=float, default=2.0, help="centring tolerance in char widths")
    ap.add_argument("--long-line", type=int, default=15, help="chars that make a line 'long'")
    ap.add_argument("--short-line", type=int, default=12, help="chars that make a line 'short'")
    ap.add_argument("--image-blocks", type=int, default=6,
                    help="a page with this few blocks and no long line is an image page")
    ap.add_argument("--image-chars", type=int, default=120,
                    help="character count under which a page may be an image page")
    ap.add_argument("--gutter", type=float, default=0.3,
                    help="coverage under this fraction of the busiest column counts as gutter")
    ap.add_argument("--min-gutter", type=int, default=12, help="narrowest gutter, in pixels")
    ap.add_argument("--min-geo", type=int, default=200,
                    help="lines needed before a parity gets its own geometry")
    ap.add_argument("--no-splice", dest="splice", action="store_false",
                    help="do not re-join fragments of the same visual line")
    ap.add_argument("--splice-overlap", type=float, default=0.5,
                    help="vertical overlap that makes two boxes one line")
    ap.add_argument("--splice-gap", type=float, default=3.0,
                    help="horizontal gap (char widths) under which boxes are joined")
    args = ap.parse_args()

    pages = load(args.ocr_dir)
    if not pages:
        sys.exit("no pNNNN.json in %s" % args.ocr_dir)

    footers = learn_footers(pages, args.footer_band, args.footer_repeat)
    bodies = {p["page"]: page_body(p, footers, args)[0] for p in pages}
    geo = learn_geometry(list(bodies.items()), args)
    out = [analyse_page(p, bodies[p["page"]], geo, args) for p in pages]

    kinds = Counter(p["kind"] for p in out)
    chars = sum(len("".join(b["text"] for b in p["blocks"])) for p in out)
    per_page = [len("".join(b["text"] for b in p["blocks"])) for p in out]
    med = median(per_page, 1)
    outliers = [p["page"] for p, n in zip(out, per_page) if n < 0.4 * med and p["kind"] == "text"]
    cols = {k: [(round(c[0]), round(c[1]), round(b[0]), round(b[1])) for c, b in v]
            for k, v in geo.items() if v}

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"pages": out, "footer_templates": sorted(footers), "columns": cols,
                   "stats": {"pages": len(out), "chars": chars,
                             "kinds": dict(kinds), "median_chars": med,
                             "low_char_pages": outliers}}, fh, ensure_ascii=False, indent=1)

    print("pages     %d  chars %d  median %d/page" % (len(out), chars, med))
    print("kinds     %s" % dict(kinds))
    print("footers   %d templates: %s" % (len(footers), sorted(footers)[:6]))
    for key in ("odd", "even"):
        if cols.get(key):
            print("columns   %s: %s" % (key, cols[key]))
    if outliers:
        print("outliers  %d text pages under 40%% of the median: %s"
              % (len(outliers), outliers[:20]))
    print("wrote     %s" % args.out)


if __name__ == "__main__":
    main()
