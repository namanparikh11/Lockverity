/**
 * Provider Health card error-display tests.
 *
 * Narrow pre-audit UX check: the per-provider rollup card on
 * the Provider Health page must not render the raw
 * ``HTTP <METHOD> https://...`` provider exception verbatim.
 * The visible text is derived from the structured
 * status / error_code / http_status fields. The raw
 * ``redacted_failure_summary`` is preserved on the underlying
 * API payload but is never present in the rendered page DOM -
 * not as text, not as a tooltip. Technical detail remains
 * available through Diagnostics and the application logs.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { ProviderHealthPage } from "@/pages/ProviderHealthPage";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

function mockProviderHealth(payload: {
  providers: string[];
  entries: Array<{
    provider: string;
    status: string;
    last_retrieved_at: string | null;
    records_returned: number;
    cache_status: string | null;
    redacted_failure_summary: string | null;
    last_error_code: string | null;
    scans_with_observations: number;
  }>;
}) {
  const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce(jsonResponse(payload));
}

function renderProviders() {
  render(
    <MemoryRouter initialEntries={["/providers"]}>
      <Routes>
        <Route path="/providers" element={<ProviderHealthPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("Provider Health card error display", () => {
  it("does not render a raw URL-bearing failure summary on an unavailable card", async () => {
    mockProviderHealth({
      providers: ["openssf"],
      entries: [
        {
          provider: "openssf",
          status: "unavailable",
          last_retrieved_at: "2026-08-25T00:00:00Z",
          records_returned: 0,
          cache_status: "miss",
          // The historical raw redacted summary that this
          // fix is replacing. The card must not display
          // this verbatim.
          redacted_failure_summary:
            "HTTP GET https://api.securityscorecards.dev/projects/github.com/o/r failed: 503 Service Unavailable",
          last_error_code: "http_503",
          scans_with_observations: 1,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-health-reason"),
      ).toBeInTheDocument();
    });
    const reason = screen.getByTestId("provider-health-reason");
    // The visible text is the concise phrase, not the raw
    // exception.
    expect(reason).toHaveTextContent("Request failed · HTTP 503");
    // No URL leaks into the visible text.
    expect(reason.textContent).not.toMatch(/api\.securityscorecards\.dev/);
    expect(reason.textContent).not.toMatch(/^HTTP GET /);
    expect(reason.textContent).not.toMatch(/github\.com/);
  });

  it("renders a rate-limited phrase when the structured code is rate_limited", async () => {
    mockProviderHealth({
      providers: ["deps_dev"],
      entries: [
        {
          provider: "deps_dev",
          status: "rate_limited",
          last_retrieved_at: "2026-08-25T00:00:00Z",
          records_returned: 0,
          cache_status: "miss",
          redacted_failure_summary:
            "HTTP GET https://api.deps.dev/insights/... 429 Too Many Requests",
          last_error_code: "rate_limited",
          scans_with_observations: 2,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-health-reason"),
      ).toBeInTheDocument();
    });
    const reason = screen.getByTestId("provider-health-reason");
    expect(reason).toHaveTextContent("Rate limited");
    expect(reason.textContent).not.toMatch(/deps\.dev/);
  });

  it("renders the operator-disabled phrase for disabled_by_operator", async () => {
    mockProviderHealth({
      providers: ["openssf"],
      entries: [
        {
          provider: "openssf",
          status: "not_requested",
          last_retrieved_at: null,
          records_returned: 0,
          cache_status: null,
          redacted_failure_summary: null,
          last_error_code: "disabled_by_operator",
          scans_with_observations: 0,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-health-reason"),
      ).toBeInTheDocument();
    });
    const reason = screen.getByTestId("provider-health-reason");
    expect(reason).toHaveTextContent("Disabled by operator");
  });

  it("does not expose the raw summary as a tooltip on the reason line", async () => {
    const raw = "HTTP GET https://api.securityscorecards.dev/... failed: 503";
    mockProviderHealth({
      providers: ["openssf"],
      entries: [
        {
          provider: "openssf",
          status: "unavailable",
          last_retrieved_at: "2026-08-25T00:00:00Z",
          records_returned: 0,
          cache_status: "miss",
          redacted_failure_summary: raw,
          last_error_code: "http_503",
          scans_with_observations: 1,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-health-reason"),
      ).toBeInTheDocument();
    });
    const reason = screen.getByTestId("provider-health-reason");
    // The raw endpoint-bearing summary is not present anywhere
    // in the rendered Providers page DOM - not as text and not
    // as a title/tooltip attribute (LV-016).
    expect(reason).not.toHaveAttribute("title");
    expect(document.body.innerHTML).not.toContain(raw);
    expect(document.body.innerHTML).not.toContain("api.securityscorecards.dev");
  });

  it("renders the explicit Not applicable phrase for not_applicable", async () => {
    mockProviderHealth({
      providers: ["openssf"],
      entries: [
        {
          provider: "openssf",
          status: "not_requested",
          last_retrieved_at: null,
          records_returned: 0,
          cache_status: null,
          redacted_failure_summary: null,
          last_error_code: "not_applicable",
          scans_with_observations: 0,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(
        screen.getByTestId("provider-health-reason"),
      ).toBeInTheDocument();
    });
    // LV-014: the explicit user-facing semantic is restored.
    const reason = screen.getByTestId("provider-health-reason");
    expect(reason).toHaveTextContent("Not applicable");
    expect(reason).not.toHaveTextContent("Disabled by operator");
  });

  it("does not render a reason line for an available provider", async () => {
    mockProviderHealth({
      providers: ["osv"],
      entries: [
        {
          provider: "osv",
          status: "available",
          last_retrieved_at: "2026-08-25T00:00:00Z",
          records_returned: 5,
          cache_status: "hit",
          redacted_failure_summary: null,
          last_error_code: null,
          scans_with_observations: 3,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(screen.getByText("OSV")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("provider-health-reason"),
    ).not.toBeInTheDocument();
  });

  it("does not render a reason line for a not_requested provider without a code", async () => {
    mockProviderHealth({
      providers: ["github"],
      entries: [
        {
          provider: "github",
          status: "not_requested",
          last_retrieved_at: null,
          records_returned: 0,
          cache_status: null,
          redacted_failure_summary: null,
          last_error_code: null,
          scans_with_observations: 0,
        },
      ],
    });
    renderProviders();
    await waitFor(() => {
      expect(screen.getByText("GitHub")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("provider-health-reason"),
    ).not.toBeInTheDocument();
  });
});
