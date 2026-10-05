"""Test Regression parser SRT — 4 bentuk input.

Run: python -m tests.test_srt_parser   (dari folder backend)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.srt_parser import SrtParser

NORMAL = """1
00:00:00,000 --> 00:00:03,000
Dulu ada pembunuh bayaran legendaris bernama Sakamoto.

2
00:00:03,000 --> 00:00:06,500
Namun setelah bertemu gadis dan menemukan cinta,
ia memutuskan meninggalkan dunia pembunuhan.

3
00:00:06,500 --> 00:00:09,000
Kini ia menjalani hidup sederhana sebagai pemilik toko kelontong.
"""

NO_INDEX = """00:00:01,000 --> 00:00:04,000
Baris pertama tanpa nomor indeks.

00:00:04,000 --> 00:00:07,000
Baris kedua tanpa nomor indeks.
"""

CRLF_BLANKS = (
    "1\r\n00:00:00,500 --> 00:00:03,000\r\nBaris satu.\r\n\r\n"
    "2\r\n00:00:03,000 --> 00:00:05,500\r\nBaris dua\r\n\r\n\r\n"
    "3\r\n00:00:05,500 --> 00:00:08,000\r\nBaris tiga\r\n"
)

LEAKED_NUMBER = """1
00:00:00,000 --> 00:00:03,000
Teknologi ini adalah salah satu dari 2

2
00:00:03,000 --> 00:00:06,000
inovasi terbesar yang pernah ada.
"""

JUNK = "ini bukan file srt sama sekali\ncuma teks acak\n"

DOT_TIME = """1
00:00:00.000 --> 00:00:02.500
Format pakai titik bukan koma.
"""


def check(name, content, expect_count, expect_text=None):
    entries = SrtParser().parse(content)
    ok = len(entries) == expect_count
    if expect_text:
        ok = ok and any(expect_text in e.text for e in entries)
    nums_leaked = any(
        e.text.strip().isdigit() for e in entries
    )
    status = "PASS" if (ok and not nums_leaked) else "FAIL"
    print(f"[{status}] {name}: {len(entries)} cue (expect {expect_count})")
    for e in entries:
        print(f"         #{e.index} {e.start} -> {e.end} | {e.text[:60]}")
    return ok and not nums_leaked


def main():
    results = [
        check("SRT normal", NORMAL, 3, "Sakamoto"),
        check("Tanpa nomor indeks", NO_INDEX, 2, "tanpa nomor"),
        check("CRLF + baris kosong", CRLF_BLANKS, 3, "Baris tiga"),
        check("Angka bocor jadi teks", LEAKED_NUMBER, 2, "inovasi terbesar"),
        check("Teks sampah", JUNK, 0),
        check("Format titik", DOT_TIME, 1, "Format pakai titik"),
    ]
    print("-" * 50)
    passed = sum(results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())