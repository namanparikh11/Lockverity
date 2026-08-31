/**
 * Demo-dataset notice tests.
 *
 * The frontend renders a small, neutral in-product notice on
 * the scan list when every listed scan carries the explicit
 * ``seeded_dataset: "demo"`` provenance marker. The notice must
 * not appear when any listed scan is a real user scan — even
 * when that scan occupies a historically "demo" numeric id
 * (1/3/4) with a historically "demo" status on the fixture
 * repository URL — and must not imply real provider data.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { AppShell } from "@/layouts/AppShell";
import { ScansIndexPage } from "@/pages/ScansIndexPage";
import type { Scan } from "@/api/types";

const DEMO_FIXTURE = "https://github.com/example-org/lockverity-fixture";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function scanFixture(overrides: Partial<Scan> & Pick<Scan, "id">): Scan {
  return {
    repository_id: 1,
    status: "completed",
    trigger_type: "manual",
    requested_ref: "main",
    resolved_commit_sha: "0123456789abcdef0123456789abcdef01234567",
    analyzer_version: "lockverity 2.1.3",
    seeded_dataset: null,
    started_at: null,
    completed_at: null,
    failure_code: null,
    failure_summary: null,
    created_at: "2026-07-17T00:00:00Z",
    updated_at: "2026-07-17T00:00:00Z",
    ...overrides,
  };
}

function mockScanListFetch(scansByRepo: Record<string, Scan[]>) {
  return vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/v1/system/info")) {
      return Promise.resolve(
        jsonResponse({
          name: "Lockverity",
          version: "2.1.3",
          tagline: "Evidence-first software supply-chain assurance",
          environment: "test",
          api_prefix: "/api/v1",
          archive_limits: {},
          pagination: {},
          provider_safety: {},
          intake: {},
        })
      );
    }
    const scansMatch = url.match(/\/api\/v1\/repositories\/(\d+)\/scans/);
    if (scansMatch) {
      const items = scansByRepo[scansMatch[1]] ?? [];
      return Promise.resolve(
        jsonResponse({
          items,
          pagination: { page: 1, page_size: 5, total: items.length, total_pages: 1 },
        })
      );
    }
    if (url.endsWith("/api/v1/repositories?page=1&page_size=50")) {
      const repoIds = Object.keys(scansByRepo);
      return Promise.resolve(
        jsonResponse({
          items: repoIds.map((id) => ({
            id: Number(id),
            source_type: "github",
            provider: "github",
            owner: "example-org",
            name: `fixture-${id}`,
            canonical_url: DEMO_FIXTURE,
            default_branch: "main",
            description: null,
            visibility: "public",
            archived: false,
            last_provider_sync_at: null,
            created_at: "2026-07-17T00:00:00Z",
            updated_at: "2026-07-17T00:00:00Z",
            original_filename: null,
          })),
        })
      );
    }
    return Promise.resolve(jsonResponse({}, 404));
  });
}

function renderScansIndex() {
  return render(
    <MemoryRouter initialEntries={["/scans"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/scans" element={<ScansIndexPage />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("demo-dataset notice on the scan list", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders the demo notice when every listed scan carries the demo marker", async () => {
    // The seeded scans sit at arbitrary ids: the notice keys on
    // the explicit marker, not on numeric ids.
    global.fetch = mockScanListFetch({
      "1": [
        scanFixture({ id: 11, status: "completed", seeded_dataset: "demo" }),
        scanFixture({ id: 12, status: "partial", seeded_dataset: "demo" }),
        scanFixture({ id: 13, status: "failed", seeded_dataset: "demo" }),
        scanFixture({ id: 14, status: "cancelled", seeded_dataset: "demo" }),
      ],
    });
    renderScansIndex();

    await waitFor(() => {
      expect(
        screen.getByText(/demo evidence: synthetic persisted dataset/i)
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(/no provider calls were made/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/not a security verdict, certification, or compliance pass-or-fail/i)
    ).toBeInTheDocument();
  });

  it("does not render the demo notice for real scans at historically demo ids on the fixture repository", async () => {
    // Real user scans occupying ids 1/3/4 with the historical
    // demo statuses and even the fixture repository URL. No
    // marker: not demo data, no notice.
    global.fetch = mockScanListFetch({
      "1": [
        scanFixture({ id: 1, status: "completed" }),
        scanFixture({ id: 3, status: "failed", failure_code: "scanner_crashed" }),
        scanFixture({ id: 4, status: "cancelled", failure_code: "operator_cancelled" }),
      ],
    });
    renderScansIndex();

    await waitFor(() => {
      expect(screen.getByText("#1")).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/demo evidence: synthetic persisted dataset/i)
    ).not.toBeInTheDocument();
  });

  it("does not render the demo notice when real scans are mixed with seeded demo scans", async () => {
    global.fetch = mockScanListFetch({
      "1": [
        scanFixture({ id: 27, status: "completed", seeded_dataset: "demo" }),
        scanFixture({ id: 42, status: "completed" }),
      ],
    });
    renderScansIndex();

    await waitFor(() => {
      expect(screen.getByText("#42")).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/demo evidence: synthetic persisted dataset/i)
    ).not.toBeInTheDocument();
  });
});
