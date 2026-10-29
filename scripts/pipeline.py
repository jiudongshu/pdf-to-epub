#!/usr/bin/env python3
"""Run the mechanical part of the PDF -> EPUB pipeline and report what is left.

    python pipeline.py book.pdf work/ --workers 8 --must-contain 谿,钺

Runs, in order, stopping at the first failure:
    1. probe_pdf.py      decide the route (text layer or OCR needed)
    2. extract_pages.py  dump every page as JPEG/PNG (skipped when a text layer is found)
    3. ocr_pages.py      OCR every page into work/ocr/pNNNN.json
    4. rebuild_text.py   paragraphs and headings into work/blocks.json

Then prints what a human still has to do: write work/spec.json and work/toc.json,
run build_epub.py and validate_epub.py. Steps already done are reused, so this
script is safe to re-run.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd, label):
    print("\n== %s ==" % label)
    print("$ " + " ".join(cmd))
    code = subprocess.call(cmd)
    if code != 0:
        sys.exit("%s failed with exit code %d" % (label, code))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("work")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--must-contain", default="",
                    help="rare characters from this book that OCR must not drop")
    ap.add_argument("--sys-path", default=os.environ.get("OCR_SYS_PATH", ""))
    ap.add_argument("--limit", type=int, default=0, help="only N pages (smoke test)")
    ap.add_argument("--start", type=int, default=1, help="first page to extract")
    ap.add_argument("--keep-flate", action="store_true",
                    help="keep non-JPEG images as PNG instead of skipping")
    args = ap.parse_args()

    pages = os.path.join(args.work, "pages")
    ocr = os.path.join(args.work, "ocr")
    blocks = os.path.join(args.work, "blocks.json")
    os.makedirs(args.work, exist_ok=True)

    run([PY, os.path.join(HERE, "probe_pdf.py"), args.pdf], "probe")

    extract = [PY, os.path.join(HERE, "extract_pages.py"), args.pdf,
               "--out", pages, "--start", str(args.start)]
    if args.limit:
        extract += ["--limit", str(args.limit)]
    run(extract, "extract page images")

    ocr_cmd = [PY, os.path.join(HERE, "ocr_pages.py"), pages, "--out", ocr,
               "--workers", str(args.workers)]
    if args.must_contain:
        ocr_cmd += ["--must-contain", args.must_contain]
    if args.sys_path:
        ocr_cmd += ["--sys-path", args.sys_path]
    if args.limit:
        ocr_cmd += ["--limit", str(args.limit)]
    run(ocr_cmd, "ocr")

    run([PY, os.path.join(HERE, "rebuild_text.py"), ocr, "--out", blocks], "rebuild")

    print("""
== next steps (human) ==
1. write %s/spec.json — metadata, front matter, offset, window
   (start from examples/spec.json; set "blocks": "%s", "ocr_dir": "%s",
    "image_dir": "%s")
2. write %s/toc.json — one entry per chapter; seed it with
     python scripts/toc_seed.py %s --pages 11-14
   then fix the titles by hand. "page" is the PRINTED page number.
3. python scripts/build_epub.py %s/spec.json --out book.epub
4. python scripts/validate_epub.py book.epub --verbose
5. read the first 3 pages of every chapter once; then ship.""" %
          (args.work, os.path.basename(blocks), ocr, pages, args.work, ocr))

    if not os.path.exists(os.path.join(args.work, "spec.json")):
        sys.exit(0)


if __name__ == "__main__":
    main()
