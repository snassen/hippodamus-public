"""The license is MIT, declared in the metadata, carried in the built package, and what came from talos is mapped.

No database is needed here. The build test builds the wheel and the sdist with uv, as test_docs_install does.
"""

import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("2128e53b52c3cce83142d798bf8edb3559ba8131", "802e8158f9a1838f923a2c6234c93d421a8cc890")
MAPPED = ["theophrastus.py", "xenophon/words.py", "xenophon/store.py", "aristotle/__init__.py", "aristotle/answerkey.py",
          "aristotle/boundary.py", "aristotle/jev.py", "aristotle/mask.py", "aristotle/policy.py",
          "aristotle/sureness.py", "aristotle/taxonomy.py"]


def license_text() -> str:
    return (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_the_license_is_the_mit_notice_of_the_talos_contributors():
    text = license_text()
    lines = text.splitlines()
    assert lines[0] == "MIT License"
    assert lines[2] == "Copyright (c) 2026 The Talos contributors"
    assert text.count("Copyright") == 1
    flat = " ".join(text.split())
    assert "Permission is hereby granted, free of charge, to any person obtaining a copy" in flat
    assert ("The above copyright notice and this permission notice shall be included in all copies or substantial"
            " portions of the Software.") in flat
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in flat
    assert text.endswith("SOFTWARE.\n")


def test_the_metadata_declares_mit_and_the_license_file():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert not any(c.startswith("License ::") for c in project.get("classifiers", []))   # PEP 639: the expression only


def test_the_attribution_page_maps_every_module_taken_from_talos():
    page = (ROOT / "docs" / "attribution.md").read_text(encoding="utf-8")
    for sha in SOURCES:
        assert sha in page
    headings = re.findall(r"^#{2,3} .*$", page, re.M)
    for module in MAPPED:
        name = module.rsplit("/", 1)[-1]
        assert (ROOT / "src" / "hippodamus" / module).is_file(), module
        assert any(name in h or f"src/hippodamus/{module.split('/')[0]}" in h for h in headings), module
    for how in ("literal", "adapted", "idea", "new"):
        assert f"| {how}" in page
    assert "not a certificate" in page


def test_the_attribution_page_keeps_the_source_notice_word_for_word():
    page = (ROOT / "docs" / "attribution.md").read_text(encoding="utf-8")
    (notice,) = re.findall(r"```text\n(.*?)```", page, re.S)
    assert notice == license_text()


def test_the_module_docstrings_still_name_what_they_took_from_talos():
    import hippodamus.aristotle as aristotle
    from hippodamus import theophrastus
    from hippodamus.aristotle import answerkey, boundary, jev, mask, policy, sureness, taxonomy
    from hippodamus.xenophon import store, words
    for module, source in [(theophrastus, "talos.rules"), (words, "talos.notes"), (store, "talos migration 044"),
                           (aristotle, "talos 0.26.0"), (answerkey, "talos.gold"), (boundary, "talos.boundary"),
                           (jev, "talos.jev"), (mask, "talos.mask"), (policy, "talos.policy"),
                           (sureness, "talos.sureness"), (taxonomy, "talos.taxonomy")]:
        assert source in " ".join(module.__doc__.split()), module.__name__


def test_the_built_wheel_and_sdist_carry_the_license_and_the_sdist_the_attribution(tmp_path):
    from test_docs_install import _uv
    out = tmp_path / "dist"
    subprocess.run([_uv(), "build", "--quiet", "--out-dir", str(out), str(ROOT)], check=True, cwd=tmp_path)
    (sdist,) = out.glob("hippodamus-0.3.2.tar.gz")
    (wheel,) = out.glob("hippodamus-0.3.2-py3-none-any.whl")
    with zipfile.ZipFile(wheel) as z:
        meta = z.read("hippodamus-0.3.2.dist-info/METADATA").decode()
        assert z.read("hippodamus-0.3.2.dist-info/licenses/LICENSE").decode() == license_text()
    assert "License-Expression: MIT" in meta and "License-File: LICENSE" in meta
    with tarfile.open(sdist) as t:
        assert t.extractfile("hippodamus-0.3.2/LICENSE").read().decode() == license_text()
        attribution = t.extractfile("hippodamus-0.3.2/docs/attribution.md").read()
    assert attribution == (ROOT / "docs" / "attribution.md").read_bytes()
