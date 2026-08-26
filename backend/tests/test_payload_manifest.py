"""Tests for the complete payload-tree integrity manifest (LV-003).

The portable payload's ``PAYLOAD-MANIFEST.json`` must cover every
regular file that ships inside the portable application. The
historical ``SHA256SUMS.txt`` covered only seven user-facing
files while the payload carried ~1017, so release verification
could succeed without verifying the actual runtime payload.

Security model under test: Lockverity is UNSIGNED. The manifest
establishes payload completeness and payload integrity relative
to the build output -- it is NOT code signing and NOT publisher
authentication. The outer distribution verification point is the
published installer / portable hash.

Coverage below:

* a valid complete payload passes; nested ``_internal`` runtime
  files and frontend assets are included;
* a modified, missing, or extra runtime file fails;
* manifest self-hashing is deterministic and non-circular;
* duplicate manifest entries (raw JSON keys and case variants),
  traversal paths, absolute/drive/UNC paths, and malformed
  entries fail;
* symlink / reparse entries fail where creating them is
  supported;
* the portable build generates the manifest and the installer
  build verifies the complete tree (static wiring contract);
* the canonical on-disk payload verifies against its manifest
  when one exists (skips with a named path otherwise).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tests.packaging_artifacts import CANONICAL_PORTABLE_ROOT, find_release_portable_root

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
MANIFEST_MODULE_PATH = BACKEND_ROOT / "scripts" / "payload_manifest.py"
PORTABLE_BUILD_SCRIPT = BACKEND_ROOT / "scripts" / "build_windows_portable.py"
INSTALLER_BUILD_SCRIPT = BACKEND_ROOT / "scripts" / "build_windows_installer.py"


def _load_manifest_module() -> object:
    """Load ``backend/scripts/payload_manifest.py`` as a module.

    The scripts tree is not a package; the module is attached
    under a synthetic name (the same pattern the portable build
    script and ``test_portable_staging.py`` use). Loading does
    not execute any build.
    """
    spec = importlib.util.spec_from_file_location(
        "lockverity_payload_manifest_under_test", MANIFEST_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load manifest module at {MANIFEST_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest_module() -> object:
    return _load_manifest_module()


def _populate(root: Path, layout: dict[str, bytes]) -> None:
    """Write a tree under ``root`` according to ``layout``."""
    for rel, content in layout.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _mini_payload_layout() -> dict[str, bytes]:
    """A miniature but structurally faithful portable payload."""
    return {
        "Lockverity.exe": b"GUI-EXE",
        "lockverity-cli.exe": b"CLI-EXE",
        "_internal/python312.dll": b"PY-RUNTIME",
        "_internal/psutil/_psutil_windows.pyd": b"PSUTIL-NATIVE",
        "_internal/webview/lib/Microsoft.Web.WebView2.Core.dll": b"WEBVIEW2",
        "_internal/alembic/cfg/alembic.ini": b"ALEMBIC-INI",
        "alembic/versions/0001_initial.py": b"MIGRATION",
        "frontend/dist/index.html": b"<html>",
        "frontend/dist/assets/app.js": b"JS",
        "brand/lockverity-symbol.png": b"PNG",
        "favicon.ico": b"ICO",
        "LICENSE": b"MIT",
        "BUILD-MANIFEST.json": b'{"product": "Lockverity"}',
        "SHA256SUMS.txt": b"abc  Lockverity.exe\n",
        "THIRD_PARTY_NOTICES.txt": b"notices",
        "PRIVACY.md": b"privacy",
        "README-PORTABLE.txt": b"readme",
    }


def _build_valid_payload(root: Path, manifest_module: object) -> Path:
    """Create the mini payload and generate its manifest. Returns the root."""
    _populate(root, _mini_payload_layout())
    manifest_module.write_payload_manifest(root, product="Lockverity", version="2.1.2-test")
    return root


def _rewrite_manifest(
    root: Path,
    manifest_module: object,
    *,
    files_override: dict | None = None,
    field_overrides: dict | None = None,
) -> None:
    """Rewrite the manifest with targeted overrides for tampering tests."""
    manifest_path = root / "PAYLOAD-MANIFEST.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if files_override is not None:
        document["files"] = files_override
    if field_overrides is not None:
        document.update(field_overrides)
    manifest_path.write_text(manifest_module.render_payload_manifest(document), encoding="utf-8")


# ---------------------------------------------------------------------
# Happy path: complete coverage
# ---------------------------------------------------------------------


def test_valid_complete_payload_passes(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    summary = manifest_module.verify_payload_tree(root)
    layout = _mini_payload_layout()
    assert summary["file_count"] == len(layout)


def test_nested_internal_runtime_files_are_included(
    tmp_path: Path, manifest_module: object
) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    for expected_rel in (
        "_internal/python312.dll",
        "_internal/psutil/_psutil_windows.pyd",
        "_internal/webview/lib/Microsoft.Web.WebView2.Core.dll",
        "_internal/alembic/cfg/alembic.ini",
        "alembic/versions/0001_initial.py",
    ):
        assert expected_rel in document["files"], (
            f"nested runtime file {expected_rel!r} must be covered by the complete payload manifest"
        )


def test_frontend_assets_are_included(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    assert "frontend/dist/index.html" in document["files"]
    assert "frontend/dist/assets/app.js" in document["files"]
    assert "brand/lockverity-symbol.png" in document["files"]


def test_manifest_covers_other_generated_records(tmp_path: Path, manifest_module: object) -> None:
    """BUILD-MANIFEST.json and SHA256SUMS.txt are covered (non-circularly)."""
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    assert "BUILD-MANIFEST.json" in document["files"]
    assert "SHA256SUMS.txt" in document["files"]
    # The manifest itself is the SOLE exclusion.
    assert "PAYLOAD-MANIFEST.json" not in document["files"]
    assert document["excluded"] == ["PAYLOAD-MANIFEST.json"]


def test_manifest_regeneration_is_deterministic_and_non_circular(
    tmp_path: Path, manifest_module: object
) -> None:
    """Regenerating the manifest for an unchanged tree is byte-identical."""
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    first = (root / "PAYLOAD-MANIFEST.json").read_bytes()
    manifest_module.write_payload_manifest(root, product="Lockverity", version="2.1.2-test")
    second = (root / "PAYLOAD-MANIFEST.json").read_bytes()
    assert first == second, (
        "payload manifest generation is not deterministic: regenerating for "
        "an unchanged tree must produce byte-identical output (the manifest "
        "excludes itself, so its presence must not change the result)"
    )
    # And the regenerated manifest still verifies.
    manifest_module.verify_payload_tree(root)


# ---------------------------------------------------------------------
# Tree tampering: modified / missing / extra / directory entries
# ---------------------------------------------------------------------


def test_modified_runtime_file_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    (root / "_internal" / "python312.dll").write_bytes(b"TAMPERED-RUNTIME")
    with pytest.raises(manifest_module.PayloadManifestError, match=r"python312\.dll"):
        manifest_module.verify_payload_tree(root)


def test_missing_runtime_file_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    (root / "_internal" / "psutil" / "_psutil_windows.pyd").unlink()
    with pytest.raises(manifest_module.PayloadManifestError, match="missing"):
        manifest_module.verify_payload_tree(root)


def test_unexpected_extra_runtime_file_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    (root / "_internal" / "injected.dll").write_bytes(b"INJECTED")
    with pytest.raises(manifest_module.PayloadManifestError, match="extra"):
        manifest_module.verify_payload_tree(root)


def test_directory_listed_as_manifest_entry_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    # Point an entry at a real directory: the verifier's actual
    # set contains only regular files, so the directory path is
    # a missing expected file.
    entry = document["files"]["Lockverity.exe"]
    document["files"]["frontend/dist"] = entry
    (root / "PAYLOAD-MANIFEST.json").write_text(
        manifest_module.render_payload_manifest(document), encoding="utf-8"
    )
    with pytest.raises(manifest_module.PayloadManifestError, match="missing"):
        manifest_module.verify_payload_tree(root)


# ---------------------------------------------------------------------
# Manifest attacks: duplicates, traversal, absolute paths
# ---------------------------------------------------------------------


def test_duplicate_json_object_key_fails(tmp_path: Path, manifest_module: object) -> None:
    """A duplicated JSON key must not be silently collapsed by the parser.

    Python dict construction would collapse a duplicate key before
    it reaches disk, so the duplicate ``files`` member is written
    into the raw JSON text directly.
    """
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    manifest_path = root / "PAYLOAD-MANIFEST.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = json.dumps(document, sort_keys=True)
    duplicated = body[:-1] + ', "files": ' + json.dumps(document["files"]) + "}"
    manifest_path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(manifest_module.PayloadManifestError, match="duplicate"):
        manifest_module.verify_payload_tree(root)


def test_duplicate_case_variant_path_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    entry = document["files"]["Lockverity.exe"]
    _rewrite_manifest(
        root,
        manifest_module,
        files_override={**document["files"], "lockverity.exe": entry},
    )
    with pytest.raises(manifest_module.PayloadManifestError, match="duplicate"):
        manifest_module.verify_payload_tree(root)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.dll",
        "a/../../outside.dll",
        "..\\outside.dll",
        "_internal\\..\\..\\outside.dll",
        "C:\\absolute\\file.dll",
        "c:/absolute/file.dll",
        "/absolute/file.dll",
        "\\\\server\\share\\file.dll",
        "dir/",
        "a//b.dll",
        "NUL-ish\x00name.dll",
    ],
)
def test_malformed_manifest_paths_fail(
    tmp_path: Path, manifest_module: object, bad_path: str
) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    document = json.loads((root / "PAYLOAD-MANIFEST.json").read_text(encoding="utf-8"))
    entry = document["files"]["Lockverity.exe"]
    _rewrite_manifest(
        root,
        manifest_module,
        files_override={**document["files"], bad_path: entry},
    )
    with pytest.raises(manifest_module.PayloadManifestError):
        manifest_module.verify_payload_tree(root)


def test_widened_exclusion_list_fails(tmp_path: Path, manifest_module: object) -> None:
    """The manifest must not be able to exclude files from coverage."""
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    _rewrite_manifest(
        root,
        manifest_module,
        field_overrides={"excluded": ["PAYLOAD-MANIFEST.json", "_internal/injected.dll"]},
    )
    with pytest.raises(manifest_module.PayloadManifestError, match="excluded"):
        manifest_module.verify_payload_tree(root)


def test_missing_manifest_file_fails(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    (root / "PAYLOAD-MANIFEST.json").unlink()
    with pytest.raises(manifest_module.PayloadManifestError, match=r"PAYLOAD-MANIFEST\.json"):
        manifest_module.verify_payload_tree(root)


def test_wrong_schema_fields_fail(tmp_path: Path, manifest_module: object) -> None:
    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    for overrides in (
        {"manifest_version": 99},
        {"algorithm": "md5"},
        {"files": {}},
    ):
        _rewrite_manifest(root, manifest_module, field_overrides=overrides)
        with pytest.raises(manifest_module.PayloadManifestError):
            manifest_module.verify_payload_tree(root)


# ---------------------------------------------------------------------
# Link / reparse defense (created where the host supports it)
# ---------------------------------------------------------------------


def _symlink_supported() -> bool:
    try:
        import os
        import tempfile

        with tempfile.TemporaryDirectory(prefix="lockverity-symlink-probe-") as tmp:
            target = Path(tmp) / "target.txt"
            target.write_bytes(b"x")
            os.symlink(target, Path(tmp) / "link.txt")
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _symlink_supported(), reason="host cannot create symlinks without privileges"
)
def test_symlink_file_entry_fails(tmp_path: Path, manifest_module: object) -> None:
    import os

    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    outside = tmp_path / "outside.dll"
    outside.write_bytes(b"OUTSIDE")
    os.symlink(outside, root / "_internal" / "link.dll")
    # Generation must refuse the linked tree...
    with pytest.raises(manifest_module.PayloadManifestError, match="symlink"):
        manifest_module.write_payload_manifest(root, product="Lockverity", version="t")
    # ...and so must verification (the manifest predates the link,
    # so the link is also an unexpected extra entry, but the walk
    # rejects the reparse point before set comparison).
    with pytest.raises(manifest_module.PayloadManifestError, match="symlink"):
        manifest_module.verify_payload_tree(root)


@pytest.mark.skipif(
    not _symlink_supported(), reason="host cannot create symlinks without privileges"
)
def test_symlink_directory_entry_fails(tmp_path: Path, manifest_module: object) -> None:
    import os

    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "escaped.dll").write_bytes(b"ESCAPED")
    os.symlink(outside_dir, root / "_internal" / "escaped")
    with pytest.raises(manifest_module.PayloadManifestError, match="symlink"):
        manifest_module.verify_payload_tree(root)


def _junction_supported() -> bool:
    """Probe whether this host can create an NTFS junction.

    Junctions are directory reparse points that -- unlike
    symlinks -- do NOT require the SeCreateSymbolicLinkPrivilege,
    so the reparse-point defense can be exercised on a stock
    non-admin Windows host.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    if os.name != "nt":
        return False
    cmd = shutil.which("cmd")
    if cmd is None:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="lockverity-junction-probe-") as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            result = subprocess.run(  # noqa: S603 - fixed argv, probe only
                [cmd, "/c", "mklink", "/J", str(Path(tmp) / "link"), str(target)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0 and (Path(tmp) / "link").exists()
    except OSError:
        return False


@pytest.mark.skipif(not _junction_supported(), reason="host cannot create NTFS junctions")
def test_junction_reparse_entry_fails(tmp_path: Path, manifest_module: object) -> None:
    """A directory junction inside the payload is a reparse point and fails.

    ``os.DirEntry.is_symlink()`` reports False for junctions; the
    walk must catch them through the Windows
    ``FILE_ATTRIBUTE_REPARSE_POINT`` attribute so an escaped
    mount cannot smuggle files into (or out of) the verified
    tree. The junction target lives under ``tmp_path`` so the
    test can never delete anything outside its sandbox.
    """
    import shutil
    import subprocess

    root = _build_valid_payload(tmp_path / "portable", manifest_module)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "escaped.dll").write_bytes(b"ESCAPED")
    link = root / "_internal" / "escaped"
    cmd = shutil.which("cmd")
    assert cmd is not None, "cmd.exe not on PATH"
    result = subprocess.run(  # noqa: S603 - fixed argv creating a sandbox junction
        [cmd, "/c", "mklink", "/J", str(link), str(outside_dir)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"junction probe failed in-test: {result.stderr!r}"
    with pytest.raises(manifest_module.PayloadManifestError, match="symlink/reparse"):
        manifest_module.verify_payload_tree(root)
    # The junction must not have been traversed: the escaped file
    # stays in its original location.
    assert (outside_dir / "escaped.dll").is_file()


# ---------------------------------------------------------------------
# Build wiring contract (static, house style)
# ---------------------------------------------------------------------


class TestBuildWiringContract:
    """The portable build generates the manifest; the installer build verifies it."""

    def test_portable_build_generates_manifest_after_sums_before_zip(self) -> None:
        text = PORTABLE_BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "_generate_payload_manifest(" in text, (
            "the portable build script must generate PAYLOAD-MANIFEST.json "
            "(LV-003 complete-tree integrity)"
        )
        # Anchor on the exact call sites in ``main`` (a bare
        # ``find`` would match the function definitions, which
        # precede ``main`` and would break the ordering check).
        sums_at = text.find("exe_hashes = _generate_sha256_sums(portable_root)")
        manifest_at = text.find("manifest_file_count = _generate_payload_manifest(")
        zip_at = text.find("zip_hash = _zip_portable(")
        assert -1 not in (sums_at, manifest_at, zip_at), (
            "could not locate the generation call sites in the portable build script"
        )
        assert sums_at < manifest_at < zip_at, (
            "PAYLOAD-MANIFEST.json must be generated after SHA256SUMS.txt (so "
            "the sums file is covered) and before the zip (so the manifest "
            "ships inside the distributed payload)"
        )

    def test_installer_build_verifies_complete_tree(self) -> None:
        text = INSTALLER_BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "_verify_payload_tree_complete(payload_root)" in text, (
            "the installer build must verify the COMPLETE payload tree against "
            "PAYLOAD-MANIFEST.json, not just the top-level SHA256SUMS entries"
        )
        assert "payload complete-tree verification failed" in text, (
            "the installer build must surface an actionable error when "
            "complete-tree verification fails"
        )
        # The verification must live inside the payload acceptance
        # path (_verify_payload_zip), not in an unused helper.
        verify_fn = text.split("def _verify_payload_zip(", 1)[1].split("\ndef ", 1)[0]
        assert "_verify_payload_tree_complete(payload_root)" in verify_fn, (
            "the complete-tree verification must be part of _verify_payload_zip "
            "so every installer build verifies the payload it embeds"
        )

    def test_installer_manifest_records_tree_file_count(self) -> None:
        text = INSTALLER_BUILD_SCRIPT.read_text(encoding="utf-8")
        assert '"payload_manifest_file_count"' in text, (
            "INSTALLER-MANIFEST.json must record the verified payload tree's "
            "file count so the release report shows the covered file set"
        )


# ---------------------------------------------------------------------
# Canonical on-disk artefact
# ---------------------------------------------------------------------


def test_canonical_payload_tree_verifies_against_manifest() -> None:
    """The canonical portable verifies completely when it carries a manifest.

    Skips (naming the expected path) when no canonical portable is
    on disk or when the on-disk portable predates the LV-003
    manifest -- the next canonical build must carry one, and the
    installer build refuses payloads without it.
    """
    module = _load_manifest_module()
    root = find_release_portable_root()
    if root is None:
        pytest.skip(
            f"no canonical portable root at {CANONICAL_PORTABLE_ROOT}; run "
            "`python backend/scripts/build_windows_portable.py` first"
        )
    manifest_path = root / "PAYLOAD-MANIFEST.json"
    if not manifest_path.is_file():
        pytest.skip(
            f"canonical portable at {root} predates the LV-003 complete-tree "
            f"manifest (no {manifest_path}); rebuild the portable so the "
            "payload carries PAYLOAD-MANIFEST.json"
        )
    summary = module.verify_payload_tree(root)
    assert summary["file_count"] > len(
        [
            "Lockverity.exe",
            "lockverity-cli.exe",
            "BUILD-MANIFEST.json",
            "LICENSE",
            "PRIVACY.md",
            "README-PORTABLE.txt",
            "THIRD_PARTY_NOTICES.txt",
        ]
    ), "the complete-tree manifest must cover more than the seven user-facing SHA256SUMS entries"
