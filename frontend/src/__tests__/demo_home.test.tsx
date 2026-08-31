/**
 * In-app demo home tests.
 *
 * The /demo page is a read-only walkthrough that resolves the
 * seeded demo dataset at runtime. These tests pin the safety
 * contract:
 *
 * - demo identity comes ONLY from the explicit
 *   ``seeded_dataset: "demo"`` provenance marker, never from
 *   numeric scan ids, repository URLs, or statuses;
 * - links are constructed from the resolved records' actual
 *   ids, so a real user scan occupying id 1, 3, or 4 is never
 *   presented as a demo scenario (even when a backend that
 *   ignores the server-side filter returns it);
 * - when no marker-carrying rows exist the page renders the
 *   bounded "Demo dataset not loaded." state with no dead
 *   scan links;
 * - the rendered production copy contains no internal
 *   milestone version labels and no development-environment
 *   instructions.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { AppShell } from "@/layouts/AppShell";
import { DemoHomePage } from "@/pages/DemoHomePage";
import type { Scan } from "@/api/types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SYSTEM_INFO = {
  name: "Lockverity",
  version: "2.1.3",
  tagline: "Evidence-first software supply-chain assurance",
  environment: "test",
  api_prefix: "/api/v1",
  archive_limits: {},
  pagination: {},
  provider_safety: {},
  intake: {},
};

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

/** The seeded demo dataset, at deliberately non-historical ids. */
function demoScanFixtures(): Scan[] {
  return [
    scanFixture({ id: 27, status: "completed", seeded_dataset: "demo" }),
    scanFixture({ id: 82, status: "partial", seeded_dataset: "demo" }),
    scanFixture({ id: 59, status: "failed", seeded_dataset: "demo", failure_code: "scanner_crashed" }),
    scanFixture({ id: 68, status: "cancelled", seeded_dataset: "demo", failure_code: "operator_cancelled" }),
  ];
}

/**
 * Real user scans occupying the historically "demo" numeric ids
 * with the historically "demo" statuses. They carry no marker
 * and must never be treated as demo data.
 */
function realScanFixtures(): Scan[] {
  return [
    scanFixture({ id: 1, status: "completed" }),
    scanFixture({ id: 3, status: "failed", failure_code: "scanner_crashed" }),
    scanFixture({ id: 4, status: "cancelled", failure_code: "operator_cancelled" }),
  ];
}

/**
 * Mock fetch for the /demo page. ``demoResponse`` is returned for
 * the seeded_dataset-filtered cross-repo scan listing; everything
 * non-demo falls through to an empty 404-shaped response.
 */
function mockDemoPageFetch(demoResponse: Scan[]) {
  return vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/v1/system/info")) {
      return Promise.resolve(jsonResponse(SYSTEM_INFO));
    }
    const parsed = new URL(url, "http://localhost");
    if (parsed.pathname.endsWith("/api/v1/scans")) {
      return Promise.resolve(
        jsonResponse({ items: demoResponse, pagination: { page: 1, page_size: 50, total: demoResponse.length, total_pages: 1 } })
      );
    }
    return Promise.resolve(jsonResponse({}, 404));
  });
}

function renderDemoPage() {
  return render(
    <MemoryRouter initialEntries={["/demo"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/demo" element={<DemoHomePage />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("in-app demo home", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("resolves demo scenarios dynamically and links to the actual scan ids", async () => {
    global.fetch = mockDemoPageFetch(demoScanFixtures());
    renderDemoPage();

    await waitFor(() => {
      expect(screen.getByText("#27")).toBeInTheDocument();
    });

    // Links are built from the resolved ids, not from any
    // hard-coded assumption.
    for (const href of [
      "/scans/27",
      "/scans/27/dependencies",
      "/scans/27/exports",
      "/scans/82",
      "/scans/59/exports",
      "/scans/68/exports",
    ]) {
      const link = document.querySelector(`a[href="${href}"]`);
      expect(link, `expected a link to ${href}`).not.toBeNull();
    }

    // The bounded absent state must not appear while the
    // dataset is present.
    expect(screen.queryByText(/demo dataset not loaded/i)).not.toBeInTheDocument();
  });

  it("never links a real user scan at a historically demo id, even if the API response is unfiltered", async () => {
    // Simulates an older backend that ignores the
    // seeded_dataset query parameter and returns every scan:
    // the client-side marker guard must keep the real scans
    // (ids 1/3/4, no marker) out of the demo scenarios.
    global.fetch = mockDemoPageFetch([...demoScanFixtures(), ...realScanFixtures()]);
    renderDemoPage();

    await waitFor(() => {
      expect(screen.getByText("#27")).toBeInTheDocument();
    });

    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    for (const href of hrefs) {
      expect(
        href,
        `demo page must not link a real scan: found ${href}`
      ).not.toMatch(/^\/scans\/(1|3|4)(\/|$)/);
    }
    // The demo scenarios still resolve to the marker scans.
    expect(hrefs).toContain("/scans/27/dependencies");
    expect(hrefs).toContain("/scans/59/exports");
    expect(hrefs).toContain("/scans/68/exports");
  });

  it("renders the bounded absent state and no scan links when only real scans exist", async () => {
    global.fetch = mockDemoPageFetch(realScanFixtures());
    renderDemoPage();

    await waitFor(() => {
      expect(screen.getByText(/demo dataset not loaded/i)).toBeInTheDocument();
    });

    // No dead or substituted scan links: the page must not
    // render any /scans/N destination while the demo dataset
    // is absent.
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.filter((href) => /^\/scans\/\d+/.test(href))).toEqual([]);

    // The user is guided toward the normal Analyze workflow.
    expect(hrefs).toContain("/analyze");
  });

  it("shows the walkthrough sections when demo data is present and hides them when absent", async () => {
    global.fetch = mockDemoPageFetch(demoScanFixtures());
    renderDemoPage();
    await waitFor(() => {
      expect(screen.getByText("#27")).toBeInTheDocument();
    });
    for (const id of ["demo-dataset-status", "demo-flow", "demo-look-for", "demo-not-claim"]) {
      expect(document.getElementById(id), `expected section #${id}`).not.toBeNull();
    }
    // The development-only command section is gone for good.
    expect(document.getElementById("demo-commands")).toBeNull();
    expect(document.getElementById("demo-dataset-absent")).toBeNull();
    cleanup();

    global.fetch = mockDemoPageFetch([]);
    renderDemoPage();
    await waitFor(() => {
      expect(screen.getByText(/demo dataset not loaded/i)).toBeInTheDocument();
    });
    expect(document.getElementById("demo-dataset-absent")).not.toBeNull();
    for (const id of ["demo-dataset-status", "demo-flow", "demo-look-for", "demo-not-claim", "demo-commands"]) {
      expect(document.getElementById(id), `did not expect section #${id}`).toBeNull();
    }
  });

  it("exposes a Demo entry in the AppShell primary nav", async () => {
    global.fetch = mockDemoPageFetch(demoScanFixtures());
    renderDemoPage();
    const navLink = screen.getByRole("link", { name: /^Demo$/ });
    expect(navLink).toBeInTheDocument();
    expect(navLink.getAttribute("href")).toBe("/demo");
  });

  it("repeatedly surfaces the synthetic-dataset disclosure", async () => {
    global.fetch = mockDemoPageFetch(demoScanFixtures());
    renderDemoPage();
    await waitFor(() => {
      expect(screen.getByText(/synthetic persisted/i)).toBeInTheDocument();
    });
    expect(
      screen.getByText(/no provider calls were made/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/no analyzed repository code is executed/i)
    ).toBeInTheDocument();
  });

  it("renders production copy free of hard-coded demo scan links, milestone labels, and development commands", async () => {
    global.fetch = mockDemoPageFetch(demoScanFixtures());
    renderDemoPage();
    await waitFor(() => {
      expect(screen.getByText("#27")).toBeInTheDocument();
    });

    // Audit the full rendered production surface (visible text
    // and link targets). The assertion is about rendered demo
    // content, not about historical source filenames.
    const html = document.body.innerHTML;
    const forbidden = [
      // Hard-coded demo scan-id assumptions.
      "/scans/1",
      "/scans/3",
      "/scans/4",
      // Internal development milestone labels.
      "v0.5",
      "v0.7",
      "v0.8",
      "v1.0",
      "v1.5",
      "v2.1.0",
      // Development-environment instructions.
      "npm install",
      "npm run dev",
      "127.0.0.1:5173",
      "127.0.0.1:8765",
      "load_demo.py",
    ];
    for (const needle of forbidden) {
      expect(
        html.includes(needle),
        `rendered demo UI must not contain ${JSON.stringify(needle)}`
      ).toBe(false);
    }
  });
});
