#!/usr/bin/env python3
"""OCR every page image into one JSON per page, resumable and parallel.

    python ocr_pages.py pages/ --out ocr/ --workers 8 --must-contain 谿,钺

Requires an OCR engine. The reference engine is rapidocr (PP-OCR family):

    pip install "rapidocr>=3.10" onnxruntime

If the engine lives in a separate site directory (a shared venv, a bundled
runtime), pass it with --sys-path "dirA;dirB".

Per-page output: ocr/pNNNN.json
    {"page": 9, "file": "p0009.jpg", "secs": 3.2,
     "lines": [[x0, y0, x1, y1, "text", 0.994], ...]}

Existing JSON files are skipped, so an interrupted run resumes for free.
"""

import argparse
import json
import multiprocessing as mp
import os
import re
import sys
import time

OUT = None
SYS_PATH = []
ENGINE = None


def image_files(folder):
    names = [n for n in sorted(os.listdir(folder))
             if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".jp2", ".tif", ".tiff"))]
    return [os.path.join(folder, n) for n in names]


def page_number(path):
    m = re.search(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else 0


def init_worker(outdir, sys_path):
    global OUT, SYS_PATH, ENGINE
    OUT = outdir
    SYS_PATH = sys_path or []
    for p in reversed(SYS_PATH):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    ENGINE = None                                   # loaded lazily, once per process


def load_engine():
    global ENGINE
    if ENGINE is not None:
        return ENGINE
    for p in reversed(SYS_PATH):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    from rapidocr import RapidOCR
    params = {"Global.log_level": "warning",
              "EngineConfig.onnxruntime.intra_op_num_threads": 1,
              "EngineConfig.onnxruntime.inter_op_num_threads": 1}
    try:
        ENGINE = RapidOCR(params=params)
    except TypeError:                               # rapidocr 1.x signature
        ENGINE = RapidOCR()
    return ENGINE


def boxes_to_rects(boxes):
    rects = []
    for box in boxes:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        rects.append((min(xs), min(ys), max(xs), max(ys)))
    return rects


def work(path):
    page = page_number(path)
    target = os.path.join(OUT, "p%04d.json" % page)
    if os.path.exists(target):
        return page, 0.0, 0, True
    started = time.time()
    engine = load_engine()
    result = engine(path)
    txts = getattr(result, "txts", None) or []
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    boxes = [] if boxes is None else list(boxes)
    scores = [1.0] * len(txts) if scores is None else [float(s) for s in scores]
    rects = boxes_to_rects(boxes) if boxes else [(0, 0, 0, 0)] * len(txts)
    lines = []
    for rect, text, score in zip(rects, txts, scores):
        text = (text or "").strip()
        if text:
            lines.append([round(rect[0], 1), round(rect[1], 1), round(rect[2], 1),
                          round(rect[3], 1), text, round(score, 4)])
    lines.sort(key=lambda l: (l[1], l[0]))
    payload = {"page": page, "file": os.path.basename(path),
               "secs": round(time.time() - started, 2), "lines": lines}
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, target)
    return page, payload["secs"], sum(len(l[4]) for l in lines), False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages")
    ap.add_argument("--sys-path", default=os.environ.get("OCR_SYS_PATH", ""),
                    help="extra site directories for the OCR engine, ';'-separated")
    ap.add_argument("--must-contain", default="",
                    help="characters that must survive OCR, comma-separated")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    files = image_files(args.pages)
    if args.limit:
        files = files[: args.limit]
    if not files:
        sys.exit("no images in %s" % args.pages)
    if args.overwrite:
        for f in files:
            t = os.path.join(args.out, "p%04d.json" % page_number(f))
            if os.path.exists(t):
                os.remove(t)

    sys_path = [p for p in args.sys_path.split(";") if p]
    todo = len(files)
    done = skipped = 0
    chars = 0
    started = time.time()
    print("ocr       %d pages, %d workers, out=%s" % (todo, args.workers, args.out))

    with mp.Pool(args.workers, initializer=init_worker,
                 initargs=(args.out, sys_path)) as pool:
        for i, (page, secs, nchars, was_cached) in enumerate(
                pool.imap_unordered(work, files, chunksize=1), start=1):
            done += 1
            skipped += 1 if was_cached else 0
            chars += nchars
            if i % 25 == 0 or i == todo:
                rate = (time.time() - started) / max(1, i - skipped)
                eta = rate * (todo - i) / 60.0
                print("progress  %d/%d  %.1fs/page  eta %.1f min  chars %d"
                      % (i, todo, rate, eta, chars))

    elapsed = (time.time() - started) / 60.0
    print("DONE      %d pages in %.1f min (%d reused), %d chars" % (done, elapsed, skipped, chars))
    check_must_contain(args.out, args.must_contain)


def check_must_contain(outdir, spec):
    chars = [c for c in (spec or "").replace(",", " ").split() if c]
    if not chars:
        return
    blob = []
    for name in sorted(os.listdir(outdir)):
        if name.endswith(".json"):
            with open(os.path.join(outdir, name), encoding="utf-8") as fh:
                blob.append("".join(l[4] for l in json.load(fh)["lines"]))
    text = "\n".join(blob)
    print("must-contain check (%d chars, %d OCR chars):" % (len(chars), len(text)))
    bad = 0
    for c in chars:
        n = text.count(c)
        if not n:
            bad += 1
        print("  %s  %d" % (c, n))
    if bad:
        print("WARNING   %d required character(s) never appeared — the engine is dropping "
              "rare glyphs; try another model before building the EPUB." % bad)
    else:
        print("ok        all required characters present")


if __name__ == "__main__":
    main()
