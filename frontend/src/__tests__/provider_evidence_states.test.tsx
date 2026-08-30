/**
 * Provider evidence-state semantics on provider-dependent
 * pages (LV-012) plus the ``not_applicable`` observation
 * detail pin (LV-014).
 *
 * The core contract: a provider-dependent view may show a
 * clean-style empty state ("No vulnerabilities recorded")
 * only when the evidence provider actually answered. When the
 * provider was unavailable, rate-limited, partial, not
 * requested, disabled, or not applicable, the empty state
 * must say so instead - a failed evidence source must never be
 * presented as a verified clean result.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { LicenceInventoryPage } from "@/pages/LicenceInventoryPage";
import { OpenSSFPosturePage } from "@/pages/OpenSSFPosturePage";
import { ProviderHealthPage } from "@/pages/ProviderHealthPage";
import { VulnerabilityExplorerPage } from "@/pages/VulnerabilityExplorerPage";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

let observationAutoId = 100;

function observationRow(overrides: Record<string, unknown>) {
  observationAutoId += 1;
  return {
    id: observationAutoId,
    scan_run_id: 1,
    component_id: null,
    provider: "osv",
    operation: "osv_vulnerability_query",
    status: "available",
    requested_at: null,
    completed_at: null,
    latency_ms: null,
    http_status: null,
    cache_status: null,
    records_returned: 0,
    error_code: null,
    error_summary: null,
    retry_count: 0,
    rate_limit_remaining: null,
    fetched_at: null,
    created_at: "2026-08-11T00:00:00Z",
    ...overrides,
  };
}

function emptyList() {
  return jsonResponse({
    items: [],
    pagination: { page: 1, page_size: 25, total: 0, total_pages: 0 },
  });
}

function renderPage(routePattern: string, initialEntry: string, element: React.ReactNode) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path={routePattern} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Mock fetch with the observation list first, then the page data. */
function mockObservationsThen(items: unknown[], pageResponse: Response, extra: Response[] = []) {
  const fetchMock = vi.fn();
  fetchMock.mockResolvedValueOnce(
    jsonResponse({
      items,
      pagination: { page: 1, page_size: 200, total: items.length, total_pages: 1 },
    }),
  );
  fetchMock.mockResolvedValueOnce(pageResponse);
  for (const response of extra) fetchMock.mockResolvedValueOnce(response);
  vi.stubGlobal("fetch", fetchMock);
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("Vulnerability explorer evidence honesty", () => {
  it("shows an unavailable-evidence empty state, not a clean result, when OSV failed", async () => {
    mockObservationsThen(
      [
        observationRow({
          provider: "osv",
          status: "unavailable",
          error_code: "http_503",
          http_status: 503,
          error_summary: "HTTP GET https://api.osv.dev/v1/query failed: 503",
        }),
      ],
      emptyList(),
    );
    renderPage("/scans/:scanId/vulnerabilities", "/scans/1/vulnerabilities", <VulnerabilityExplorerPage />);
    await waitFor(() => {
      expect(screen.getByText("Vulnerability evidence unavailable")).toBeInTheDocument();
    });
    expect(screen.queryByText("No vulnerabilities recorded")).not.toBeInTheDocument();
  });

  it("aggregates: a later unrelated success must not read as checked", async () => {
    // Component 1 failed, component 2 later succeeded. The
    // chronologically latest row is a success; the page must
    // still report unavailable evidence.
    mockObservationsThen(
      [
        observationRow({
          component_id: 1,
          status: "unavailable",
          error_code: "http_500",
        }),
        observationRow({
          component_id: 2,
          status: "available",
        }),
      ],
      emptyList(),
    );
    renderPage("/scans/:scanId/vulnerabilities", "/scans/1/vulnerabilities", <VulnerabilityExplorerPage />);
    await waitFor(() => {
      expect(screen.getByText("Vulnerability evidence unavailable")).toBeInTheDocument();
    });
  });

  it("keeps rate-limited evidence distinct and incomplete", async () => {
    mockObservationsThen(
      [observationRow({ status: "rate_limited", error_code: "rate_limited", http_status: 429 })],
      emptyList(),
    );
    renderPage("/scans/:scanId/vulnerabilities", "/scans/1/vulnerabilities", <VulnerabilityExplorerPage />);
    await waitFor(() => {
      expect(screen.getByText("Vulnerability evidence unavailable")).toBeInTheDocument();
    });
    expect(screen.getByText(/rate-limited/i)).toBeInTheDocument();
  });

  it("keeps partial evidence distinct from clean", async () => {
    mockObservationsThen(
      [observationRow({ status: "partial", error_code: "partial_batch" })],
      emptyList(),
    );
    renderPage("/scans/:scanId/vulnerabilities", "/scans/1/vulnerabilities", <VulnerabilityExplorerPage />);
    await waitFor(() => {
      expect(screen.getByText("Vulnerability evidence incomplete")).toBeInTheDocument();
    });
    expect(screen.queryByText("No vulnerabilities recorded")).not.toBeInTheDocument();
  });

  it("justifies the clean empty state only when OSV actually answered", async () => {
    mockObservationsThen([observationRow({ status: "available" })], emptyList());
    renderPage("/scans/:scanId/vulnerabilities", "/scans/1/vulnerabilities", <VulnerabilityExplorerPage />);
    await waitFor(() => {
      expect(screen.getByText("No vulnerabilities recorded")).toBeInTheDocument();
    });
    expect(screen.getByText(/OSV was queried for this scan/i)).toBeInTheDocument();
  });
});

describe("Licence inventory evidence honesty", () => {
  it("reports unavailable licence evidence instead of a clean empty state", async () => {
    mockObservationsThen(
      [
        observationRow({
          provider: "deps_dev",
          operation: "deps_dev_enrichment",
          status: "unavailable",
          error_code: "http_503",
        }),
      ],
      emptyList(),
    );
    renderPage("/scans/:scanId/licences", "/scans/1/licences", <LicenceInventoryPage />);
    await waitFor(() => {
      expect(screen.getByText("Licence evidence unavailable")).toBeInTheDocument();
    });
    expect(screen.queryByText("No licence assertions recorded")).not.toBeInTheDocument();
  });

  it("keeps partial enrichment distinct from clean", async () => {
    mockObservationsThen(
      [
        observationRow({
          provider: "deps_dev",
          operation: "deps_dev_enrichment",
          status: "partial",
          error_code: "partial_batch",
        }),
      ],
      emptyList(),
    );
    renderPage("/scans/:scanId/licences", "/scans/1/licences", <LicenceInventoryPage />);
    await waitFor(() => {
      expect(screen.getByText("Licence enrichment incomplete")).toBeInTheDocument();
    });
  });
});

describe("OpenSSF posture evidence honesty", () => {
  it("reports unavailable OpenSSF evidence instead of a clean empty state", async () => {
    mockObservationsThen(
      [
        observationRow({
          provider: "openssf",
          operation: "openssf_scorecard_read",
          status: "unavailable",
          error_code: "http_503",
        }),
      ],
      emptyList(),
    );
    renderPage("/scans/:scanId/openssf", "/scans/1/openssf", <OpenSSFPosturePage />);
    await waitFor(() => {
      expect(screen.getByText("OpenSSF evidence not available")).toBeInTheDocument();
    });
    expect(screen.queryByText("No OpenSSF checks imported")).not.toBeInTheDocument();
  });
});

describe("Provider Health observation detail (LV-014 / LV-016)", () => {
  function renderScanProviders(items: unknown[]) {
    // ProviderHealthPage issues two calls on mount: the
    // per-scan observation list, then the cross-scan rollup.
    mockObservationsThen(
      items,
      jsonResponse({ providers: [], entries: [] }),
    );
    renderPage("/scans/:scanId/providers", "/scans/1/providers", <ProviderHealthPage />);
  }

  it("renders the explicit Not applicable phrase for a not_applicable observation", async () => {
    renderScanProviders([
      observationRow({
        provider: "openssf",
        operation: "openssf_scorecard_read",
        status: "not_requested",
        error_code: "not_applicable",
        error_summary:
          "OpenSSF Scorecard is only available for public GitHub repositories.",
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText("Not applicable")).toBeInTheDocument();
    });
    // The semantic is neither a failure nor a clean success.
    expect(screen.queryByText("Provider unavailable")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/only available for public GitHub/i),
    ).not.toBeInTheDocument();
  });

  it("keeps raw endpoint-bearing summaries out of the observation detail", async () => {
    const raw =
      "HTTP GET https://api.deps.dev/v3/systems/npm/packages/left-pad failed: 503 Service Unavailable";
    renderScanProviders([
      observationRow({
        provider: "deps_dev",
        operation: "deps_dev_enrichment",
        status: "unavailable",
        error_code: "http_503",
        error_summary: raw,
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText("Request failed · HTTP 503")).toBeInTheDocument();
    });
    expect(document.body.innerHTML).not.toContain(raw);
    expect(document.body.innerHTML).not.toContain("api.deps.dev");
  });
});
