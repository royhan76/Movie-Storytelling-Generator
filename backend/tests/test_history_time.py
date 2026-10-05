"""Verifikasi formatProjectTime dari HistoryPanel.tsx (logika dicopy persis)."""
import re

CASES = {
    "20261003-233816-ea6bf9": "03/10/2026 23:38",
    "20261003-221136-f88520": "03/10/2026 22:11",
    "20261003-225301-3a8ba7": "03/10/2026 22:53",
    "bukan-format": "bukan-format",
}


def fmt(pid: str) -> str:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", pid)
    if not m:
        return pid
    _, year, month, day, hour, minute = m.groups()
    return f"{day}/{month}/{year} {hour}:{minute}"


fails = 0
for pid, expected in CASES.items():
    got = fmt(pid)
    ok = got == expected
    fails += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {pid:26} -> {got:18} (harap: {expected})")

print("ALL PASS" if fails == 0 else f"{fails} FAIL")