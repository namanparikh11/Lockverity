"""Tests for the exporters."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC

from app.exporters import (
    CycloneDxExporter,
    FindingsCsvExporter,
    FindingsJsonExporter,
    SarifStaticFindingsExporter,
)
from app.models.component import Component, ComponentVersionSource
from app.models.finding import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    FindingStatus,
)
from app.models.repository import (
    Repository,
    RepositoryProvider,
    RepositorySourceType,
    RepositoryVisibility,
)
from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType
from app.providers.results import ProviderSuccess, ProviderUnavailable


def _make_session_data(session):
    repo = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="o",
        name="n",
        canonical_url="https://github.com/o/n",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repo)
    session.flush()
    scan = ScanRun(
        repository_id=repo.id,
        trigger_type=ScanTriggerType.MANUAL,
        status=ScanStatus.COMPLETED,
    )
    session.add(scan)
    session.flush()
    component = Component(
        scan_run_id=scan.id,
        manifest_id=0,
        ecosystem="npm",
        package_name="lodash",
        version="4.17.21",
        version_source=ComponentVersionSource.LOCKFILE,
        package_url="pkg:npm/lodash@4.17.21",
        direct=True,
    )
    session.add(component)
    session.flush()
    finding = Finding(
        scan_run_id=scan.id,
        repository_id=repo.id,
        rule_id="LOCK-VULN-001",
        category=FindingCategory.VULNERABILITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        title="Direct dependency lodash is affected",
        summary="Lodash 4.17.21 is affected by an advisory",
        remediation="Upgrade lodash",
        location_path="package.json",
        location_start_line=10,
        location_end_line=10,
        stable_key="a" * 64,
        status=FindingStatus.OPEN,
    )
    session.add(finding)
    # A finding without a location_path - must be skipped in SARIF.
    finding_no_loc = Finding(
        scan_run_id=scan.id,
        repository_id=repo.id,
        rule_id="LOCK-VULN-007",
        category=FindingCategory.VULNERABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        title="Provider unavailable",
        summary="OSV provider was unavailable",
        remediation="Re-run",
        location_path=None,
        stable_key="b" * 64,
        status=FindingStatus.OPEN,
    )
    session.add(finding_no_loc)
    session.commit()
    return scan.id


def test_cyclonedx_exporter_returns_success(session) -> None:
    scan_id = _make_session_data(session)
    exporter = CycloneDxExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    bom = json.loads(result.data)
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    assert any(c["purl"] == "pkg:npm/lodash@4.17.21" for c in bom["components"])
    assert any(v["id"] == "LOCK-VULN-001" for v in bom["vulnerabilities"])


def test_cyclonedx_exporter_returns_unavailable_for_unknown_scan(session) -> None:
    exporter = CycloneDxExporter(lambda: session)
    result = exporter.export(scan_run_id=99_999)
    assert isinstance(result, ProviderUnavailable)


def test_findings_json_exporter_returns_success(session) -> None:
    scan_id = _make_session_data(session)
    exporter = FindingsJsonExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    doc = json.loads(result.data)
    assert doc["schema"] == "lockverity.findings.v1"
    assert doc["scan_run_id"] == scan_id
    assert len(doc["findings"]) >= 2


def test_findings_json_exporter_is_byte_deterministic_across_calls(
    session,
) -> None:
    """Two exports of the same immutable scan must emit the same
    bytes. The deterministic contract relies on a stable
    ``fetched_at`` timestamp derived from the scan's own
    ``completed_at`` (or ``created_at``) rather than the
    wall-clock moment the export was triggered.
    """
    scan_id = _make_session_data(session)
    exporter = FindingsJsonExporter(lambda: session)
    first = exporter.export(scan_run_id=scan_id)
    second = exporter.export(scan_run_id=scan_id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    assert first.data == second.data
    # The ``fetched_at`` value is sourced from the scan,
    # not the wall clock. The field name is the original
    # ``lockverity.findings.v1`` contract.
    doc = json.loads(first.data)
    assert "fetched_at" in doc
    assert doc["fetched_at"].endswith("Z") or doc["fetched_at"].endswith("+00:00")
    # Schema v1 stability: the legacy ``exported_at`` key
    # is not silently added by a v1 export; the wire
    # format is unchanged.
    assert "exported_at" not in doc


def test_findings_csv_exporter_is_byte_deterministic_across_calls(
    session,
) -> None:
    """Two CSV exports of the same immutable scan must emit the
    same bytes. The deterministic contract relies on a stable
    ``exported_at`` header derived from the scan's own
    ``completed_at`` (or ``created_at``). The original
    public CSV contract is the ``exported_at=`` key, which
    the v2.0.6 closure restored from the cycle-6
    incorrectly-renamed ``fetched_at=``.
    """
    scan_id = _make_session_data(session)
    exporter = FindingsCsvExporter(lambda: session)
    first = exporter.export(scan_run_id=scan_id)
    second = exporter.export(scan_run_id=scan_id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    assert first.data == second.data
    # The header carries the deterministic ``fetched_at``
    # derived from the scan. The field name is the
    # Original public contract: the v2.0.6 closure
    # restored the historical ``exported_at=`` CSV
    # header. The deterministic value is sourced from
    # the scan's own ``completed_at`` (or
    # ``created_at``).
    text = first.data.decode("utf-8")
    assert "exported_at=" in text
    assert "exported_at=1970" not in text  # the scan has a real timestamp
    # Schema stability: the JSON-schema ``fetched_at``
    # key is not silently added to the CSV header by
    # the v1 exporter.
    assert "fetched_at=" not in text


def test_findings_csv_exporter_completed_at_fallback_to_created_at(
    session,
) -> None:
    """When the scan has a ``created_at`` but no
    ``completed_at`` (e.g. an in-flight scan), the
    ``exported_at`` header falls back to ``created_at``
    and is still stable across calls.
    """
    from datetime import datetime

    from app.models.repository import (
        Repository,
        RepositoryProvider,
        RepositorySourceType,
        RepositoryVisibility,
    )
    from app.models.scan_run import ScanRun, ScanTriggerType

    repo = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="fallback-csv",
        name="repo",
        canonical_url="https://github.com/fallback-csv/repo",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repo)
    session.flush()
    fixed_created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    scan = ScanRun(
        repository_id=repo.id,
        trigger_type=ScanTriggerType.MANUAL,
        created_at=fixed_created,
    )
    session.add(scan)
    session.commit()
    exporter = FindingsCsvExporter(lambda: session)
    first = exporter.export(scan_run_id=scan.id)
    second = exporter.export(scan_run_id=scan.id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    assert first.data == second.data
    text = first.data.decode("utf-8")
    assert "exported_at=2026-01-01T00:00:00Z" in text


def test_findings_csv_exporter_both_timestamps_missing_uses_epoch(
    session,
) -> None:
    """A scan with neither ``completed_at`` nor
    ``created_at`` produces the deterministic epoch
    placeholder. SQLAlchemy's NOT NULL constraint on
    ``created_at`` prevents us from round-tripping a
    null through the real schema; the helper is
    exercised here with a minimal stand-in scan.
    """
    from types import SimpleNamespace

    from app.exporters.findings_json import _stable_fetched_at

    scan = SimpleNamespace(completed_at=None, created_at=None)
    assert _stable_fetched_at(scan) == "1970-01-01T00:00:00Z"


def test_findings_csv_exporter_malformed_timestamp_uses_epoch(
    session,
) -> None:
    """A non-datetime ``created_at`` / ``completed_at``
    value is treated as missing; the header is the
    deterministic epoch placeholder, not a raise.
    """
    from types import SimpleNamespace

    from app.exporters.findings_json import _stable_fetched_at

    scan = SimpleNamespace(completed_at="not-a-date", created_at="garbage")
    assert _stable_fetched_at(scan) == "1970-01-01T00:00:00Z"


def test_findings_csv_exporter_utc_formatting_is_stable(session) -> None:
    """The ``exported_at`` header value always ends in
    ``Z``; the formatter is deterministic across calls.
    """
    scan_id = _make_session_data(session)
    exporter = FindingsCsvExporter(lambda: session)
    first = exporter.export(scan_run_id=scan_id)
    second = exporter.export(scan_run_id=scan_id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    text = first.data.decode("utf-8")
    # Locate the ``exported_at=`` value in the header line.
    header_line = text.splitlines()[0]
    assert "exported_at=" in header_line
    exported_value = header_line.split("exported_at=", 1)[1].strip()
    assert exported_value.endswith("Z")


def test_findings_json_exporter_completed_at_fallback_to_created_at(
    session,
) -> None:
    """When the scan has a ``created_at`` but no
    ``completed_at`` (e.g. an in-flight scan), the
    ``fetched_at`` value falls back to ``created_at`` and
    is still stable across calls.
    """
    from datetime import datetime

    from app.models.repository import (
        Repository,
        RepositoryProvider,
        RepositorySourceType,
        RepositoryVisibility,
    )
    from app.models.scan_run import ScanRun, ScanStatus, ScanTriggerType

    # We build a scan with created_at set explicitly and
    # completed_at left null.
    repo = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="fallback",
        name="repo",
        canonical_url="https://github.com/fallback/repo",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repo)
    session.flush()
    fixed_created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    scan = ScanRun(
        repository_id=repo.id,
        trigger_type=ScanTriggerType.MANUAL,
        status=ScanStatus.RUNNING,
        created_at=fixed_created,
    )
    session.add(scan)
    session.commit()
    exporter = FindingsJsonExporter(lambda: session)
    first = exporter.export(scan_run_id=scan.id)
    second = exporter.export(scan_run_id=scan.id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    assert first.data == second.data
    doc = json.loads(first.data)
    assert doc["fetched_at"] == "2026-01-01T00:00:00Z"


def test_findings_json_exporter_both_timestamps_missing_uses_epoch() -> None:
    """The deterministic helper returns the epoch placeholder
    when both ``completed_at`` and ``created_at`` are null.

    SQLAlchemy's NOT NULL constraint on ``created_at``
    prevents us from round-tripping a null through the
    real schema; the helper is exercised here with a
    minimal stand-in scan that has neither timestamp.
    """
    from types import SimpleNamespace

    from app.exporters.findings_json import _stable_fetched_at

    scan = SimpleNamespace(completed_at=None, created_at=None)
    assert _stable_fetched_at(scan) == "1970-01-01T00:00:00Z"


def test_findings_json_exporter_malformed_timestamp_uses_epoch() -> None:
    """A non-datetime ``created_at`` value is treated as missing;
    the helper returns the deterministic epoch placeholder
    rather than raising.
    """
    from types import SimpleNamespace

    from app.exporters.findings_json import _stable_fetched_at

    # Malformed: a string the ORM would never produce, but
    # the helper must still be defensive.
    scan = SimpleNamespace(completed_at="not-a-date", created_at="garbage")
    assert _stable_fetched_at(scan) == "1970-01-01T00:00:00Z"


def test_findings_json_exporter_utc_formatting_is_stable(session) -> None:
    """The ``fetched_at`` value always ends in ``Z`` (not
    ``+00:00``); the formatter is deterministic across
    calls.
    """
    scan_id = _make_session_data(session)
    exporter = FindingsJsonExporter(lambda: session)
    first = exporter.export(scan_run_id=scan_id)
    second = exporter.export(scan_run_id=scan_id)
    assert isinstance(first, ProviderSuccess)
    assert isinstance(second, ProviderSuccess)
    doc = json.loads(first.data)
    assert doc["fetched_at"].endswith("Z")
    # Re-serialise the document to confirm the
    # ``sort_keys=True`` contract.
    assert doc == json.loads(second.data)


def test_findings_json_exporter_schema_v1_key_compatibility(session) -> None:
    """The ``lockverity.findings.v1`` schema identifier is
    preserved; the ``fetched_at`` key is the original
    public name; no silent new keys are added.
    """
    scan_id = _make_session_data(session)
    exporter = FindingsJsonExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    doc = json.loads(result.data)
    assert doc["schema"] == "lockverity.findings.v1"
    # The original public key is the only deterministic
    # timestamp in the document.
    assert "fetched_at" in doc
    # A consumer that pinned the previous public wire
    # format (``exported_at``) does not silently see a
    # new key.
    assert "exported_at" not in doc


def _null_session():
    """A no-op context manager used to keep the test bodies linear."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        yield

    return _cm()


def test_findings_csv_exporter_protects_against_formula_injection(session) -> None:
    scan_id = _make_session_data(session)
    # Add a finding whose title starts with '=' to test formula
    # injection protection.
    finding = Finding(
        scan_run_id=scan_id,
        repository_id=1,
        rule_id="LOCK-WF-011",
        category=FindingCategory.WORKFLOW,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.HIGH,
        title="=cmd|'/c calc'!A1",
        summary="+dangerous",
        remediation="-take action",
        location_path="ci.yml",
        stable_key="c" * 64,
        status=FindingStatus.OPEN,
    )
    session.add(finding)
    session.commit()
    exporter = FindingsCsvExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    text = result.data.decode("utf-8")
    # The header line starts with '#', which spreadsheets ignore.
    assert text.startswith("# lockverity findings export")
    # Skip the comment line, then parse the data rows.
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    reader = csv.reader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    header = rows[0]
    for row in rows[1:]:
        title = row[header.index("title")]
        # The leading char must not be a formula trigger.
        assert title[:1] not in {"=", "+", "-", "@"}
    # We also verify the redaction via direct inspection: every
    # '=' / '+' / '-' / '@' that originally appeared in a cell is
    # preceded by a U+200B.
    assert "\u200b=" in text


def test_findings_csv_exporter_escapes_embedded_quotes(session) -> None:
    scan_id = _make_session_data(session)
    finding = Finding(
        scan_run_id=scan_id,
        repository_id=1,
        rule_id="LOCK-WF-X",
        category=FindingCategory.WORKFLOW,
        severity=FindingSeverity.LOW,
        confidence=FindingConfidence.MEDIUM,
        title='he said "hi"',
        summary="nothing",
        remediation="nothing",
        stable_key="d" * 64,
        status=FindingStatus.OPEN,
    )
    session.add(finding)
    session.commit()
    exporter = FindingsCsvExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    text = result.data.decode("utf-8")
    assert 'he said ""hi""' in text


def test_sarif_exporter_only_includes_location_anchored_findings(session) -> None:
    scan_id = _make_session_data(session)
    exporter = SarifStaticFindingsExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    sarif = json.loads(result.data)
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    # The location-anchored finding is present; the no-location
    # finding is not forced into SARIF.
    rule_ids = {r["ruleId"] for r in run["results"]}
    assert "LOCK-VULN-001" in rule_ids
    # The skipped count is recorded in properties.
    assert run["properties"]["lockverity:findings_skipped_no_location"] >= 1
    # SARIF results with no location are never produced.
    for r in run["results"]:
        assert "physicalLocation" in r["locations"][0]


def test_sarif_exporter_severity_levels(session) -> None:
    scan_id = _make_session_data(session)
    exporter = SarifStaticFindingsExporter(lambda: session)
    result = exporter.export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    sarif = json.loads(result.data)
    levels = {r["level"] for r in sarif["runs"][0]["results"]}
    # The test data has at least one high-severity finding.
    assert "error" in levels


def test_sarif_exporter_unknown_scan_returns_unavailable(session) -> None:
    exporter = SarifStaticFindingsExporter(lambda: session)
    result = exporter.export(scan_run_id=99_999)
    assert isinstance(result, ProviderUnavailable)


def test_all_exporters_share_protocol() -> None:
    for cls in (
        CycloneDxExporter,
        FindingsCsvExporter,
        FindingsJsonExporter,
        SarifStaticFindingsExporter,
    ):
        assert hasattr(cls, "format")
        assert isinstance(cls.format, str)


# ----------------------------------------------------------------------
# Evidence-coverage semantics (LV-012)
# ----------------------------------------------------------------------

from app.models.provider_observation import (  # noqa: E402
    ProviderObservation,
    ProviderStatus,
)

_scan_counter = 0


def _make_scan_without_findings(session, observations: list[dict]) -> int:
    """Create a completed scan with zero findings plus raw observation rows.

    ``observations`` entries are ``dict(provider=, status=,
    error_code=)`` kwargs for :class:`ProviderObservation`.
    """
    global _scan_counter
    _scan_counter += 1
    repo = Repository(
        source_type=RepositorySourceType.GITHUB,
        provider=RepositoryProvider.GITHUB,
        owner="o",
        name=f"cov{_scan_counter}",
        canonical_url=f"https://github.com/o/cov{_scan_counter}",
        visibility=RepositoryVisibility.PUBLIC,
    )
    session.add(repo)
    session.flush()
    scan = ScanRun(
        repository_id=repo.id,
        trigger_type=ScanTriggerType.MANUAL,
        status=ScanStatus.COMPLETED,
    )
    session.add(scan)
    session.flush()
    for _index, obs in enumerate(observations, start=1):
        session.add(
            ProviderObservation(
                scan_run_id=scan.id,
                component_id=obs.get("component_id"),
                provider=obs["provider"],
                operation=obs.get("operation", f"{obs['provider']}_op"),
                status=obs["status"],
                records_returned=0,
                cache_status="miss",
                error_code=obs.get("error_code"),
            )
        )
    session.commit()
    return scan.id


_ALL_OK = [
    {"provider": "osv", "status": ProviderStatus.AVAILABLE},
    {"provider": "deps_dev", "status": ProviderStatus.AVAILABLE},
    {"provider": "openssf", "status": ProviderStatus.CACHED},
]

_DEPS_UNAVAILABLE = [
    {"provider": "osv", "status": ProviderStatus.AVAILABLE},
    {
        "provider": "deps_dev",
        "component_id": 1,
        "status": ProviderStatus.UNAVAILABLE,
        "error_code": "provider_unavailable",
    },
    {
        "provider": "deps_dev",
        "component_id": 2,
        "status": ProviderStatus.AVAILABLE,
    },
    {"provider": "openssf", "status": ProviderStatus.AVAILABLE},
]

_ALL_DISABLED = [
    {
        "provider": "osv",
        "status": ProviderStatus.NOT_REQUESTED,
        "error_code": "disabled_by_operator",
    },
    {
        "provider": "deps_dev",
        "status": ProviderStatus.NOT_REQUESTED,
        "error_code": "disabled_by_operator",
    },
    {
        "provider": "openssf",
        "status": ProviderStatus.NOT_REQUESTED,
        "error_code": "disabled_by_operator",
    },
]

_DEPS_RATE_LIMITED = [
    {"provider": "osv", "status": ProviderStatus.AVAILABLE},
    {
        "provider": "deps_dev",
        "component_id": 1,
        "status": ProviderStatus.RATE_LIMITED,
        "error_code": "rate_limited",
    },
    {"provider": "openssf", "status": ProviderStatus.AVAILABLE},
]

_DEPS_PARTIAL = [
    {"provider": "osv", "status": ProviderStatus.AVAILABLE},
    {
        "provider": "deps_dev",
        "component_id": 1,
        "status": ProviderStatus.PARTIAL,
        "error_code": "partial_batch",
    },
    {"provider": "openssf", "status": ProviderStatus.AVAILABLE},
]


def _csv_lines(result: ProviderSuccess) -> list[str]:
    return result.data.decode("utf-8").splitlines()


def test_csv_distinguishes_provider_success_from_provider_failure_at_zero_findings(
    session,
) -> None:
    """A zero-finding CSV must state whether the evidence sources answered."""

    ok_id = _make_scan_without_findings(session, _ALL_OK)
    fail_id = _make_scan_without_findings(session, _DEPS_UNAVAILABLE)
    ok_text = FindingsCsvExporter(lambda: session).export(scan_run_id=ok_id).data.decode("utf-8")
    fail_text = (
        FindingsCsvExporter(lambda: session).export(scan_run_id=fail_id).data.decode("utf-8")
    )

    def _header(text: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if line.startswith("# ") and "=" in line:
                key, _, value = line[2:].partition("=")
                if key in {"provider_coverage", "provider_status"}:
                    fields[key] = value
        return fields

    ok_header = _header(ok_text)
    fail_header = _header(fail_text)
    # Successful provider + zero findings is a verified clean
    # grid; provider failure + zero findings is NOT.
    assert ok_header["provider_coverage"] == "ok"
    assert fail_header["provider_coverage"] == "degraded"
    assert ok_header["provider_coverage"] != fail_header["provider_coverage"]
    # The per-provider detail names the degraded source even
    # though another component later succeeded (LV-011 rule).
    assert fail_header["provider_status"] == (
        "deps_dev:unavailable,openssf:available,osv:available"
    )
    # The all-ok scan reports every provider as checked; a
    # cached final row aggregates to the same successful
    # "available" state (cached evidence is checked evidence).
    assert ok_header["provider_status"] == ("deps_dev:available,openssf:available,osv:available")
    # No fake finding row was invented to communicate
    # coverage: the data grid contains only the column header
    # row and zero finding rows in both exports.
    for text in (ok_text, fail_text):
        data_rows = [line for line in text.splitlines() if line and not line.startswith("#")]
        assert len(data_rows) == 1
        assert "scan_run_id" in data_rows[0]
        assert "title" in data_rows[0]
    # The coverage lines live in the '#' comment block that
    # spreadsheet importers skip; they cannot become cells.
    for text in (ok_text, fail_text):
        for line in text.splitlines():
            if "provider" in line:
                assert line.startswith("#")


def test_csv_disabled_and_not_requested_is_not_provider_failure(session) -> None:
    scan_id = _make_scan_without_findings(session, _ALL_DISABLED)
    text = FindingsCsvExporter(lambda: session).export(scan_run_id=scan_id).data.decode("utf-8")
    assert "# provider_coverage=not_requested" in text
    assert "# provider_status=deps_dev:not_requested,openssf:not_requested,osv:not_requested"
    assert "degraded" not in text


def test_csv_rate_limited_and_partial_remain_incomplete(session) -> None:
    rate_id = _make_scan_without_findings(session, _DEPS_RATE_LIMITED)
    partial_id = _make_scan_without_findings(session, _DEPS_PARTIAL)
    rate_text = (
        FindingsCsvExporter(lambda: session).export(scan_run_id=rate_id).data.decode("utf-8")
    )
    partial_text = (
        FindingsCsvExporter(lambda: session).export(scan_run_id=partial_id).data.decode("utf-8")
    )
    assert "# provider_coverage=degraded" in rate_text
    assert "deps_dev:rate_limited" in rate_text
    assert "# provider_coverage=degraded" in partial_text
    assert "deps_dev:partial" in partial_text


def test_sarif_carries_provider_coverage_in_run_properties(session) -> None:
    ok_id = _make_scan_without_findings(session, _ALL_OK)
    fail_id = _make_scan_without_findings(session, _DEPS_UNAVAILABLE)
    for scan_id, expected in ((ok_id, "ok"), (fail_id, "degraded")):
        result = SarifStaticFindingsExporter(lambda: session).export(scan_run_id=scan_id)
        assert isinstance(result, ProviderSuccess)
        sarif = json.loads(result.data)
        assert sarif["version"] == "2.1.0"
        props = sarif["runs"][0]["properties"]
        assert props["lockverity:provider-coverage"] == expected
        # No result was fabricated to communicate coverage.
        assert sarif["runs"][0]["results"] == []
    fail_props = json.loads(
        SarifStaticFindingsExporter(lambda: session).export(scan_run_id=fail_id).data
    )["runs"][0]["properties"]
    assert fail_props["lockverity:provider-status"] == (
        "deps_dev:unavailable,openssf:available,osv:available"
    )


def test_sarif_disabled_providers_are_not_reported_as_failure(session) -> None:
    scan_id = _make_scan_without_findings(session, _ALL_DISABLED)
    result = SarifStaticFindingsExporter(lambda: session).export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    props = json.loads(result.data)["runs"][0]["properties"]
    assert props["lockverity:provider-coverage"] == "not_requested"
    assert props["lockverity:provider-coverage"] != "degraded"


def test_cyclonedx_15_carries_provider_coverage_in_metadata_properties(session) -> None:
    ok_id = _make_scan_without_findings(session, _ALL_OK)
    fail_id = _make_scan_without_findings(session, _DEPS_UNAVAILABLE)
    for scan_id, expected in ((ok_id, "ok"), (fail_id, "degraded")):
        result = CycloneDxExporter(lambda: session).export(scan_run_id=scan_id)
        assert isinstance(result, ProviderSuccess)
        bom = json.loads(result.data)
        assert bom["bomFormat"] == "CycloneDX"
        assert bom["specVersion"] == "1.5"
        props = {p["name"]: p["value"] for p in bom["metadata"]["properties"]}
        # Standard CycloneDX property shape (name/value strings)
        # only; no schema extension was invented.
        assert props["lockverity:provider-coverage"] == expected
        assert props["lockverity:provider-status"]
        # No vulnerability entry was fabricated.
        assert bom["vulnerabilities"] == []
    fail_bom = json.loads(CycloneDxExporter(lambda: session).export(scan_run_id=fail_id).data)
    fail_props = {p["name"]: p["value"] for p in fail_bom["metadata"]["properties"]}
    assert fail_props["lockverity:provider-coverage"] == "degraded"
    assert fail_props["lockverity:provider-status"] == (
        "deps_dev:unavailable,openssf:available,osv:available"
    )


def test_cyclonedx_15_disabled_providers_are_not_reported_as_degraded(session) -> None:
    scan_id = _make_scan_without_findings(session, _ALL_DISABLED)
    result = CycloneDxExporter(lambda: session).export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    props = {p["name"]: p["value"] for p in json.loads(result.data)["metadata"]["properties"]}
    assert props["lockverity:provider-coverage"] == "not_requested"


def test_findings_json_still_lists_raw_provider_observations(session) -> None:
    """The JSON export keeps its stronger evidence shape (no regression)."""

    scan_id = _make_scan_without_findings(session, _DEPS_UNAVAILABLE)
    result = FindingsJsonExporter(lambda: session).export(scan_run_id=scan_id)
    assert isinstance(result, ProviderSuccess)
    doc = json.loads(result.data)
    statuses = {(p["provider"], p["status"]) for p in doc["providers"]}
    assert ("deps_dev", "unavailable") in statuses
    assert ("deps_dev", "available") in statuses
    assert doc["findings"] == []
