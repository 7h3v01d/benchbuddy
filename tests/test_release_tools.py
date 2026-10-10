"""The release manifest: checksums are right, tampering is caught, and the version metadata is consistent."""

import hashlib
import importlib.util
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_manifest", ROOT / "tools" / "release_manifest.py")
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "dist" / "BenchBuddy"
    (app / "_internal" / "sub").mkdir(parents=True)
    (app / "BenchBuddy.exe").write_bytes(b"MZ fake exe")
    (app / "_internal" / "sub" / "data.bin").write_bytes(bytes(range(256)) * 10)
    return app


def test_manifest_round_trip_and_tamper_detection(tmp_path, capsys):
    from benchbuddy import __version__
    app = _fake_app(tmp_path)
    report = tmp_path / "st.txt"
    report.write_text("ALL PASSED in 1.0 s\n")
    assert rm.main([str(app), "--self-test-report", str(report)]) == 0
    dist = app.parent
    sums = dist / f"BenchBuddy-{__version__}-SHA256SUMS.txt"
    lines = sums.read_text().splitlines()
    assert len(lines) == 3                                      # 2 files + the zip
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest
    zname = next(n for n in (x.split("  ", 1)[1] for x in lines) if n.endswith(".zip"))
    with zipfile.ZipFile(dist / zname) as z:
        assert sorted(z.namelist()) == ["BenchBuddy/BenchBuddy.exe", "BenchBuddy/_internal/sub/data.bin"]
        assert z.testzip() is None
    notes = (dist / f"BenchBuddy-{__version__}-RELEASE.txt").read_text()
    assert __version__ in notes and "ALL PASSED" in notes and "Executable BenchBuddy/BenchBuddy.exe" in notes

    capsys.readouterr()
    assert rm.main(["--verify", str(sums)]) == 0
    (app / "_internal" / "sub" / "data.bin").write_bytes(b"tampered")
    assert rm.main(["--verify", str(sums)]) == 1
    assert "CHANGED  BenchBuddy/_internal/sub/data.bin" in capsys.readouterr().out


def _built(tmp_path):
    from benchbuddy import __version__
    app = _fake_app(tmp_path)
    assert rm.main([str(app)]) == 0
    return app, app.parent / f"BenchBuddy-{__version__}-SHA256SUMS.txt"


def test_verify_fails_when_any_manifest_file_is_missing(tmp_path, capsys):
    app, sums = _built(tmp_path)
    (app / "_internal" / "sub" / "data.bin").unlink()              # exe still present and correct
    capsys.readouterr()
    assert rm.verify(sums) == 1
    assert rm.main(["--verify", str(sums)]) == 1
    assert "MISSING  BenchBuddy/_internal/sub/data.bin" in capsys.readouterr().out


def test_allow_missing_is_explicit_and_still_catches_changes(tmp_path):
    app, sums = _built(tmp_path)
    for f in app.rglob("*"):                                         # only the zip is left
        if f.is_file():
            f.unlink()
    assert rm.verify(sums) == 1
    assert rm.main(["--verify", str(sums), "--allow-missing"]) == 0
    zipf = next(sums.parent.glob("*.zip"))
    zipf.write_bytes(zipf.read_bytes() + b"x")
    assert rm.main(["--verify", str(sums), "--allow-missing"]) == 1


@pytest.mark.parametrize("line", ["not a sums line", "abc  BenchBuddy/BenchBuddy.exe",
                                  "0" * 64 + "  ../../outside.txt", "0" * 64 + "  "])
def test_malformed_or_escaping_lines_fail(tmp_path, line):
    app, sums = _built(tmp_path)
    sums.write_text(sums.read_text() + line + "\n")
    assert rm.verify(sums) == 1
    assert rm.verify(sums, allow_missing=True) == 1


def test_verify_refuses_an_empty_match(tmp_path):
    sums = tmp_path / "SUMS.txt"
    sums.write_text("0" * 64 + "  nothing/here.exe\n")
    assert rm.main(["--verify", str(sums)]) == 1             # nothing verified is not a pass


def test_version_is_single_sourced():
    from benchbuddy import __version__
    py = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    dyn = 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text()
    assert dyn or (py and py.group(1) == __version__), "pyproject.toml version differs from benchbuddy.__version__"
    spec = (ROOT / "benchbuddy.spec").read_text()
    assert "__version__" in spec and "version=_version_resource()" in spec
