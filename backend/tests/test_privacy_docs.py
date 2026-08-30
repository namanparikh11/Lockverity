"""Static privacy/documentation regression tests."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_privacy_policy_documents_actual_coordinates_and_boundaries() -> None:
    privacy = _read("docs/privacy.md")
    for required in (
        "GitHub repository retrieval",
        "OSV",
        "deps.dev",
        "OpenSSF Scorecard",
        "Archive uploads",
        "Local runtime and storage",
        "Optional GitHub token",
        "Telemetry and analytics",
        "package ecosystem, package name, and observed version",
        "repository owner, and repository name",
        "no client, cache, or network operation",
    ):
        assert required in privacy
    assert "No PII" not in privacy


def test_known_false_network_claims_are_removed() -> None:
    portable = _read("docs/windows-portable.md")
    installer = _read("docs/windows-installer.md")
    auto_run = _read("backend/app/api/v0_3.py")
    new_repository = _read("frontend/src/pages/NewRepositoryPage.tsx")
    intake = _read("backend/app/api/intake.py")

    assert "network calls the operator explicitly configured" not in portable
    assert "never opens a network socket" not in portable
    assert "The only network access is the operator's local" not in installer
    assert "never makes a network\n    call to an upstream provider" not in auto_run
    assert "no code is fetched or executed" not in new_repository
    assert "and start a scan" not in intake


def test_new_policy_relative_links_resolve() -> None:
    for relative in (
        "docs/privacy.md",
        "docs/code-signing-policy.md",
        "docs/windows-portable.md",
        "docs/windows-installer.md",
    ):
        source = REPO_ROOT / relative
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            path_part = target.split("#", 1)[0]
            assert (source.parent / path_part).resolve().is_file(), (
                f"broken relative link in {relative}: {target}"
            )


def test_code_signing_policy_is_bounded_and_current() -> None:
    policy = _read("docs/code-signing-policy.md")
    assert "Lockverity v2.1.2 is currently unsigned" in policy
    assert "There is no current SignPath-signed Lockverity release" in policy
    assert "SignPath Foundation approval was not obtained" in policy
    assert "Lockverity has no SignPath signing integration" in policy
    assert "portable ZIP itself does not receive Authenticode" in policy
    assert "definitive artifact configuration is present today" in policy
    assert "immutable source tag is not moved" in policy
