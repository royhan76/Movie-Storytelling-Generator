"""Jalankan semua test Plan 1 sekaligus.

Run: python -m tests.run_all
"""
import subprocess
import sys
from pathlib import Path

TESTS = [
    "tests.test_srt_parser",
    "tests.test_clip_validator",
    "tests.test_chunking",
    "tests.test_target_duration",
    "tests.test_target_vs_film",
    "tests.test_section_mode",
    "tests.test_pipeline",
]


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    failed = []

    for name in TESTS:
        print(f"\n{'=' * 55}\n  {name}\n{'=' * 55}")
        proc = subprocess.run(
            [sys.executable, "-m", name],
            cwd=backend,
            capture_output=True,
            text=True,
        )
        out = proc.stdout.strip()
        if out:
            print(out)
        if proc.returncode != 0:
            if proc.stderr.strip():
                print(proc.stderr.strip()[-800:])
            failed.append(name)

    print(f"\n{'=' * 55}")
    if failed:
        print(f"FAILED: {len(failed)}/{len(TESTS)} -> {', '.join(failed)}")
        return 1
    print(f"ALL {len(TESTS)} TEST MODULES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())