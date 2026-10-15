"""The repository reads as a generic project, and the package installs and imports from a clean home.

No database is needed here. The install test builds the sdist and wheel with uv (the wheel from the sdist, so the sdist
is proven complete), installs the wheel with plain pip into a fresh virtual environment, and imports every part from
an empty home directory, with nothing of the source tree or the developer's settings on the path.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import unicodedata
import zipfile
from pathlib import Path

import pytest

import hippodamus

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).name  # holds the patterns themselves, so the scans below pass it over
PARTS = ["solon", "xenophon", "xenophon.store", "xenophon.words", "theophrastus", "pheidippides", "thucydides",
         "aristotle", "aristotle.answerkey", "aristotle.boundary", "aristotle.jev", "aristotle.mask",
         "aristotle.policy", "aristotle.sureness", "aristotle.taxonomy"]

# Personal and instance names that must never come back: kept as sha256 digests of the lowercased, accent-free word
# (or mail domain), so this file does not repeat what it guards against.
FORBIDDEN_DIGESTS = {
    "403ad5235763a4f3885aad6dea508e53e8262f73e02fe00f5e8752a14a9aa92b",
    "48f1e0c4ee4fd97d5594c01137300912eba9f70ea768194e7025059d20cde8d3",
    "4dd68e2ab3a30973318ea903e088b3d3480655ef4236109fe47272c1c1582880",
    "5667ef73dac709effcfdc518a98ea9238365ec62b0eb5f478392e28818a2efd5",
    "8c35d5016df87734f28cfe8770af9f68c9dc5d7c1c3f60cd81e3188cc123d5be",
    "a509b6ba8b6f4b7126b70328285322a434fd91b74431854ce35181f28136400f",
    "aa475625981493dc52fceb3cb9a39f54d22903284538f5562d2118678b6e6149",
    "c61989f22ef4eae4bbdcd818818d17393681dcf6594d133b89b56774fc1253d5",
}
# Shapes of instance data, whatever the name: home directories, a home shorthand, tailnet hosts, real mail addresses.
# The home roots are written in two pieces so that a path scanner reading this file does not take the patterns
# themselves for a home directory; the compiled patterns are the same.
INSTANCE_SHAPES = {
    "a macOS home path": re.compile("/" + r"Users/[^/\s`'\"]+"),
    "a Linux home path": re.compile("/" + r"home/[a-z_][^/\s`'\"]*"),
    "a Windows home path": re.compile(r"[A-Za-z]:\\+Users\\+", re.I),
    "a home shorthand": re.compile(r"(?<![\w.$])~/"),
    "a tailnet host": re.compile(r"\.ts\.net\b", re.I),
    "a mail address": re.compile(r"\b[\w.+-]+@(?!example\.(?:invalid|com|org|net)\b)[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b",
                                 re.I),
}


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def traces(text: str) -> list[str]:
    """What in a text looks personal or instance-specific, described without repeating the value."""
    found = [shape for shape, pattern in INSTANCE_SHAPES.items() if pattern.search(text)]
    folded = fold(text)
    words = set(re.findall(r"[a-z0-9]+", folded)) | set(re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", folded))
    found += ["a forbidden name" for w in words if hashlib.sha256(w.encode()).hexdigest() in FORBIDDEN_DIGESTS]
    return found


def tracked_files() -> list[Path]:
    """The files git tracks; a checkout without git (an unpacked sdist) falls back to every file under the root."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [ROOT / p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache"}
        return [p for p in ROOT.rglob("*") if p.is_file() and not skip & set(p.relative_to(ROOT).parts)]


def kept_private() -> set[str]:
    """The exact paths `.publishignore` keeps out of every public snapshot (its anchored lines): owner tooling and
    working notes that never ship, so the trace scan passes over them. A public snapshot has no `.publishignore` and
    none of those files, so there every file is scanned."""
    path = ROOT / ".publishignore"
    if not path.is_file():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line[1:] for line in lines if line.startswith("/") and not line.endswith("/") and "*" not in line}


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_the_trace_check_catches_what_it_is_for():
    assert traces("see /Users/jane/x") == ["a macOS home path"]          # an invented, generic user name
    assert traces("see /home/jane/x") == ["a Linux home path"]
    assert traces("cd ~/repos") == ["a home shorthand"]
    assert traces("mail a.person@company.test") == ["a mail address"]
    assert traces("agent@example.invalid, 127.0.0.1, hippodamus_test, $HOME/x") == []


def test_no_tracked_file_carries_a_personal_or_instance_trace():
    findings = []
    private = kept_private()
    for path in tracked_files():
        if path.is_symlink() or not path.is_file() or path.name == SELF:
            continue
        if path.relative_to(ROOT).as_posix() in private:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings += [f"{path.relative_to(ROOT)}: {what}" for what in traces(text)]
    assert findings == []


def test_the_project_metadata_is_neutral_and_has_no_runtime_dependencies():
    p = project()
    assert p["name"] == "hippodamus"
    assert p["version"] == hippodamus.__version__ == "0.3.2"
    assert p["dependencies"] == []
    assert p["requires-python"] == ">=3.14"
    assert p["readme"] == "README.md"
    assert p["description"] and traces(p["description"]) == []
    assert "'s products" not in p["description"]


def test_the_docs_say_how_to_install_contribute_and_report_a_vulnerability():
    readme = (ROOT / "README.md").read_text()
    assert "## Install" in readme and "[tool.uv.sources]" in readme and "git+<repository-url>@vX.Y.Z" in readme
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    assert "HIPPODAMUS_TEST_DSN" in contributing and "port=5441" in contributing and "uv sync --locked" in contributing
    security = (ROOT / "SECURITY.md").read_text()
    assert "privately" in security and "public issue" in security
    if (ROOT / "AGENTS.md").exists() or (ROOT / "CLAUDE.md").exists():   # a public snapshot carries neither
        assert (ROOT / "CLAUDE.md").resolve() == (ROOT / "AGENTS.md").resolve()


def test_ci_runs_the_full_suite_on_a_disposable_scratch_database_without_secrets():
    from conftest import scratch_only
    ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    dsn = re.search(r"^\s*HIPPODAMUS_TEST_DSN:\s*(.+)$", ci, re.M).group(1).strip()
    scratch_only(dsn)                                                       # the guard accepts the CI database
    assert "5441:5432" in ci and "image: postgres:" in ci
    assert 'python-version: "3.14"' in ci and "uv sync --locked" in ci and "pytest" in ci
    assert "skipped" in ci                                                  # a skip fails the run
    assert "shell: bash" in ci                                              # pipefail: tee does not hide a failure
    assert "secrets." not in ci and "permissions:\n  contents: read" in ci
    assert traces(ci) == []


def _uv() -> str:
    uv = os.environ.get("UV") or shutil.which("uv")
    assert uv, "uv is needed to build the package (it is the build front end this project uses)"
    return uv


def test_the_sdist_and_wheel_build_and_carry_only_neutral_metadata(tmp_path):
    out = tmp_path / "dist"
    subprocess.run([_uv(), "build", "--quiet", "--out-dir", str(out), str(ROOT)], check=True, cwd=tmp_path)
    (sdist,) = out.glob("hippodamus-0.3.2.tar.gz")
    (wheel,) = out.glob("hippodamus-0.3.2-py3-none-any.whl")
    with tarfile.open(sdist) as t:
        names = t.getnames()
        assert "hippodamus-0.3.2/README.md" in names and "hippodamus-0.3.2/src/hippodamus/__init__.py" in names
        for m in t.getmembers():
            if m.isfile() and not m.name.endswith(SELF):
                assert traces(t.extractfile(m).read().decode("utf-8", "replace")) == [], m.name
    with zipfile.ZipFile(wheel) as z:
        meta = z.read("hippodamus-0.3.2.dist-info/METADATA").decode()
        for name in (n for n in z.namelist() if not n.endswith(SELF)):
            assert traces(z.read(name).decode("utf-8", "replace")) == [], name
    assert "Version: 0.3.2" in meta and "Requires-Dist" not in meta
    assert "Shared parts for local-first products" in meta


def test_the_wheel_installs_and_imports_from_a_clean_home_with_nothing_else(tmp_path):
    out, home, venv = tmp_path / "dist", tmp_path / "home", tmp_path / "venv"
    home.mkdir()
    subprocess.run([_uv(), "build", "--quiet", "--wheel", "--out-dir", str(out), str(ROOT)], check=True,
                   cwd=tmp_path)
    (wheel,) = out.glob("hippodamus-0.3.2-py3-none-any.whl")
    clean = {"HOME": str(home), "PATH": os.defpath, "PIP_NO_INDEX": "1", "PIP_NO_CACHE_DIR": "1",
             "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "LANG": "C.UTF-8"}
    base = getattr(sys, "_base_executable", sys.executable)
    subprocess.run([base, "-m", "venv", str(venv)], check=True, env=clean, cwd=home)
    python = venv / "bin" / "python"
    subprocess.run([python, "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)], check=True, env=clean,
                   cwd=home)
    probe = ("import importlib, importlib.metadata as md, json, hippodamus, sys\n"
             f"parts = {PARTS!r}\n"
             "for p in parts: importlib.import_module('hippodamus.' + p)\n"
             "print(json.dumps({'version': hippodamus.__version__, 'dist': md.version('hippodamus'),\n"
             "    'requires': md.requires('hippodamus'), 'file': hippodamus.__file__, 'parts': len(parts),\n"
             "    'psycopg': 'psycopg' in sys.modules}))\n")
    r = subprocess.run([python, "-I", "-c", probe], check=True, env=clean, cwd=home, capture_output=True, text=True)
    got = json.loads(r.stdout)
    assert got["version"] == got["dist"] == "0.3.2"
    assert got["requires"] is None                                           # no runtime dependencies
    assert Path(got["file"]).is_relative_to(venv) and not Path(got["file"]).is_relative_to(ROOT)
    assert got["parts"] == len(PARTS) and got["psycopg"] is False
    assert list(home.iterdir()) == []                                        # nothing written to the home
