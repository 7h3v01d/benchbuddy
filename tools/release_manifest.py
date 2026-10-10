"""Package a built app folder as a release zip and write SHA-256 checksums.

    python tools/release_manifest.py dist/BenchBuddy [--self-test-report dist/selftest-report.txt]

Writes next to the folder:
  BenchBuddy-<version>-<platform>.zip     the folder, files in sorted order
  BenchBuddy-<version>-SHA256SUMS.txt     sha256sum format: every file in the folder + the zip
  BenchBuddy-<version>-RELEASE.txt        what was built, with what, and the self-test result
Verify a download or an installed copy later (any OS, no sha256sum needed):
    python tools/release_manifest.py --verify dist/BenchBuddy-<version>-SHA256SUMS.txt
or on Linux/macOS:  sha256sum -c BenchBuddy-<version>-SHA256SUMS.txt   (run in that folder)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import platform
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(sums_file: Path) -> int:
    """Check every line of a SHA256SUMS file. Missing entries are skipped (e.g. the zip, once unpacked)."""
    base, bad, ok, missing = sums_file.parent, 0, 0, 0
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        path = base / name
        if not path.is_file():
            missing += 1
        elif sha256(path) == digest:
            ok += 1
        else:
            bad += 1
            print(f"CHANGED  {name}")
    print(f"{ok} OK, {bad} changed, {missing} not present")
    return 1 if bad or not ok else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("app_dir", type=Path, nargs="?")
    ap.add_argument("--self-test-report", type=Path)
    ap.add_argument("--verify", type=Path, metavar="SHA256SUMS", help="check files against a sums file")
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.verify)
    if a.app_dir is None:
        ap.error("give the built app folder, or --verify SUMS")
    app_dir: Path = a.app_dir.resolve()
    if not app_dir.is_dir():
        print(f"no such folder: {app_dir}", file=sys.stderr)
        return 2
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "benchbuddy" / "__init__.py").read_text()).group(1)
    plat = {"win32": "win64", "darwin": "macos"}.get(sys.platform, sys.platform)
    out = app_dir.parent
    stem = f"{app_dir.name}-{version}"
    files = sorted(p for p in app_dir.rglob("*") if p.is_file())

    zpath = out / f"{stem}-{plat}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            z.write(f, f.relative_to(out).as_posix())

    sums = [f"{sha256(f)}  {f.relative_to(out).as_posix()}" for f in files]
    sums.append(f"{sha256(zpath)}  {zpath.name}")
    (out / f"{stem}-SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    constraints = ROOT / "constraints-release.txt"
    st = a.self_test_report.read_text(encoding="utf-8") if a.self_test_report and a.self_test_report.is_file() \
        else "(not run)\n"
    exe = next((f for f in files if f.name.lower() in ("benchbuddy.exe", "benchbuddy")), None)
    info = [
        f"BenchBuddy {version} ({plat})",
        f"Built      {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Python     {platform.python_version()} on {platform.platform()}",
        f"Files      {len(files)} in {app_dir.name}/, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB",
        f"Zip        {zpath.name}  sha256 {sha256(zpath)}",
    ]
    if exe:
        info.append(f"Executable {exe.relative_to(out).as_posix()}  sha256 {sha256(exe)}")
    if constraints.is_file():
        info.append(f"Pins       constraints-release.txt  sha256 {sha256(constraints)}")
    info += ["", "Self-test", "---------", st.rstrip()]
    (out / f"{stem}-RELEASE.txt").write_text("\n".join(info) + "\n", encoding="utf-8")
    print(f"wrote {zpath.name}, {stem}-SHA256SUMS.txt, {stem}-RELEASE.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
