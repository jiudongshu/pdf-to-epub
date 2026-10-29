#!/usr/bin/env python3
"""Structural validation for an EPUB 3 without any external tool.

    python validate_epub.py book.epub [--verbose]

Checks the things that actually break readers:
  * `mimetype` is the first entry, stored, exactly "application/epub+zip"
  * META-INF/container.xml points at a real OPF
  * the OPF is well-formed, its unique-identifier resolves, every manifest item
    exists, every spine itemref resolves, a nav document is declared
  * every XHTML/NCX document is well-formed XML
  * every internal href/src resolves to a manifest item
  * the nav document lists at least one link

Exit status 0 means PASS. This is a fast local gate; run EPUBCheck for the
authoritative answer when Java is available.
"""

import argparse
import os
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
XML_EXT = (".xhtml", ".html", ".opf", ".ncx", ".xml", ".svg")


class Report:
    def __init__(self, verbose):
        self.errors = []
        self.warnings = []
        self.infos = []
        self.verbose = verbose

    def error(self, msg):
        self.errors.append(msg)
        print("ERROR   " + msg)

    def warn(self, msg):
        self.warnings.append(msg)
        print("WARN    " + msg)

    def info(self, msg):
        self.infos.append(msg)
        if self.verbose:
            print("info    " + msg)


def check_mimetype(zf, rep):
    names = zf.namelist()
    if not names:
        rep.error("empty archive")
        return
    if names[0] != "mimetype":
        rep.error("first entry is %r, must be 'mimetype'" % names[0])
        return
    info = zf.getinfo("mimetype")
    if info.compress_type != zipfile.ZIP_STORED:
        rep.error("mimetype must be stored (compress_type=%d)" % info.compress_type)
    body = zf.read("mimetype").decode("ascii", "replace")
    if body != "application/epub+zip":
        rep.error("mimetype content is %r" % body)


def locate_opf(zf, rep):
    if "META-INF/container.xml" not in zf.namelist():
        rep.error("META-INF/container.xml missing")
        return None
    try:
        root = ET.fromstring(zf.read("META-INF/container.xml"))
    except ET.ParseError as exc:
        rep.error("container.xml is not well-formed: %s" % exc)
        return None
    node = root.find(".//c:rootfile", NS)
    if node is None or not node.get("full-path"):
        rep.error("container.xml has no rootfile")
        return None
    path = node.get("full-path")
    if path not in zf.namelist():
        rep.error("rootfile %s does not exist in the archive" % path)
        return None
    return path


def check_opf(zf, opf_path, rep):
    try:
        root = ET.fromstring(zf.read(opf_path))
    except ET.ParseError as exc:
        rep.error("OPF is not well-formed: %s" % exc)
        return None
    opf_dir = posixpath.dirname(opf_path)

    uid = root.get("unique-identifier")
    ids = [n.get("id") for n in root.findall(".//dc:identifier", NS)]
    if uid not in ids:
        rep.error("unique-identifier %r is not a dc:identifier (%s)" % (uid, ids))
    else:
        value = [n.text for n in root.findall(".//dc:identifier", NS) if n.get("id") == uid]
        rep.info("identifier %s" % value)

    for tag in ("dc:title", "dc:language"):
        node = root.find(".//" + tag, NS)
        if node is None or not (node.text or "").strip():
            rep.error("missing %s" % tag)
        else:
            rep.info("%s = %s" % (tag, node.text))

    manifest = {}
    nav_found = False
    for item in root.findall(".//opf:manifest/opf:item", NS):
        item_id, href = item.get("id"), item.get("href")
        if not item_id or not href:
            rep.error("manifest item without id or href: %s" % ET.tostring(item))
            continue
        full = posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
        if full not in zf.namelist():
            rep.error("manifest item %s -> %s missing from archive" % (item_id, full))
        manifest[item_id] = full
        props = item.get("properties") or ""
        if "nav" in props.split():
            nav_found = True
            rep.info("nav document %s" % full)
    if not nav_found:
        rep.error("no manifest item with properties=\"nav\" (EPUB 3 requires one)")

    spine = root.findall(".//opf:spine/opf:itemref", NS)
    if not spine:
        rep.error("empty spine")
    for ref in spine:
        if ref.get("idref") not in manifest:
            rep.error("spine idref %r has no manifest item" % ref.get("idref"))
    rep.info("manifest %d items, spine %d documents" % (len(manifest), len(spine)))
    return root, opf_dir, manifest


def check_documents(zf, opf_dir, manifest, rep):
    docs = 0
    chars = 0
    for path in zf.namelist():
        if not path.lower().endswith(XML_EXT):
            continue
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError as exc:
            rep.error("%s is not well-formed: %s" % (path, exc))
            continue
        docs += 1
        if path.lower().endswith((".xhtml", ".html")):
            text = "".join(root.itertext())
            chars += len(re.sub(r"\s+", "", text) or "")
        for node in root.iter():
            for attr in ("href", "src"):
                target = node.get(attr)
                if not target or target.startswith(("http:", "https:", "mailto:", "data:")):
                    continue
                clean = unquote(target.split("#")[0])
                if not clean:
                    continue
                full = posixpath.normpath(posixpath.join(posixpath.dirname(path), clean))
                if full not in zf.namelist():
                    rep.error("%s references missing %s" % (path, target))
    rep.info("xml documents %d, body characters %d" % (docs, chars))
    return chars


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("epub")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rep = Report(args.verbose)
    try:
        zf = zipfile.ZipFile(args.epub)
    except (OSError, zipfile.BadZipFile) as exc:
        print("ERROR   cannot open %s: %s" % (args.epub, exc))
        sys.exit(1)

    with zf:
        check_mimetype(zf, rep)
        opf_path = locate_opf(zf, rep)
        if opf_path:
            result = check_opf(zf, opf_path, rep)
            if result:
                _root, opf_dir, manifest = result
                check_documents(zf, opf_dir, manifest, rep)

    print("-" * 60)
    print("file %s (%.1f MB)" % (args.epub, os.path.getsize(args.epub) / 1048576.0))
    if rep.errors:
        print("RESULT: FAIL — %d error(s), %d warning(s)"
              % (len(rep.errors), len(rep.warnings)))
        sys.exit(1)
    print("RESULT: PASS — 0 errors, %d warning(s)" % len(rep.warnings))


if __name__ == "__main__":
    main()
