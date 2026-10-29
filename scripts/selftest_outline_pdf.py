"""Build a tiny PDF with a nested outline to test outline_toc.py.

    python selftest_outline_pdf.py        # writes tmp_outline/outline.pdf
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tmp_outline")


def utf16(text):
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def build():
    # object number -> body. Pages 3/5/7, their content streams 4/6/8.
    obj = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 9 0 R /Dests 15 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 4 0 R >>",
        4: "<< /Length 0 >>",
        5: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 6 0 R >>",
        6: "<< /Length 0 >>",
        7: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 8 0 R >>",
        8: "<< /Length 0 >>",
        9: "<< /Type /Outlines /First 10 0 R /Last 12 0 R /Count 4 >>",
        # level 1, direct array destination
        10: ("<< /Title %s /Parent 9 0 R /Next 11 0 R /First 13 0 R "
             "/Dest [3 0 R /XYZ 0 400 0] >>" % utf16("第一章 词源")),
        # level 1, action destination
        11: ("<< /Title (Chapter Two) /Parent 9 0 R /Prev 10 0 R /Next 12 0 R "
             "/A << /S /GoTo /D [5 0 R /Fit] >> >>"),
        # level 1, named destination resolved through /Dests
        12: ("<< /Title %s /Parent 9 0 R /Prev 11 0 R /Dest (ch3) >>" % utf16("第三章")),
        # level 2
        13: "<< /Title (Section 1.1) /Parent 10 0 R /Dest [7 0 R /Fit] >>",
        15: "<< /ch3 [7 0 R /Fit] >>",
    }
    parts = [b"%PDF-1.7\n"]
    offsets = {}
    for num in sorted(obj):
        offsets[num] = sum(len(p) for p in parts)
        parts.append(("%d 0 obj\n%s\nendobj\n" % (num, obj[num])).encode("latin-1"))
    xref_at = sum(len(p) for p in parts)
    size = max(obj) + 1
    xref = ["xref\n0 %d\n" % size, "0000000000 65535 f \n"]
    for num in range(1, size):
        xref.append("%010d 00000 n \n" % offsets.get(num, 0))
    parts.append("".join(xref).encode("latin-1"))
    parts.append(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (size, xref_at)).encode("latin-1"))
    return b"".join(parts)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "outline.pdf")
    with open(path, "wb") as fh:
        fh.write(build())
    print(path, os.path.getsize(path), "bytes")
