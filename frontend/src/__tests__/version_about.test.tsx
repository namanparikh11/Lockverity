/**
 * Frontend version-consistency and About-page tests.
 *
 * The product version lives on the backend; the frontend never
 * hardcodes a version in a public-facing surface. The About page
 * copy is derived from the actual code paths the application ships.
 *
 * The About page renders the version from /system/info so the
 * page cannot drift from the running backend. The tests below
 * mock /system/info and assert against the resolved version.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { AppShell } from "@/layouts/AppShell";
import { AppearanceProvider } from "@/appearance";
import { AboutPage } from "@/pages/AboutPage";

const SYSTEM_INFO_BODY = {
  name: "Lockverity",
  version: "2.1.1",
  tagline: "Evidence-first software supply-chain assurance",
  environment: "test",
  api_prefix: "/api/v1",
  archive_limits: {},
  pagination: {},
  provider_safety: {},
  intake: {},
};

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockSystemInfo() {
  global.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/system/info")) {
      return Promise.resolve(
        new Response(JSON.stringify(SYSTEM_INFO_BODY), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      );
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
}

describe("version consistency", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("renders the version from /system/info in the AppShell footer", async () => {
    mockSystemInfo();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>placeholder</div>} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    await waitFor(() => {
      expect(screen.getByText("v2.1.1")).toBeInTheDocument();
    });
  });

  it("falls back to a neutral marker when /system/info is unavailable", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"));
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>placeholder</div>} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    // The footer must not show a hardcoded "vX.Y.Z" string and
    // must not show an alarming red error. It should fall back
    // to the product name in the version slot of the footer.
    await waitFor(() => {
      const matches = screen.getAllByText("Lockverity");
      expect(matches.length).toBeGreaterThan(0);
      expect(screen.queryByText(/^v\d+\.\d+\.\d+$/)).not.toBeInTheDocument();
    });
  });
});

describe("About page current product copy", () => {
  it("renders the canonical version from /system/info in the hero", async () => {
    mockSystemInfo();
    render(
      <MemoryRouter initialEntries={["/about"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/about" element={<AboutPage />} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    await waitFor(() => {
      expect(screen.getByTestId("about-version")).toHaveTextContent("v2.1.1");
    });
    expect(screen.getByTestId("about-version-footer")).toHaveTextContent(
      "v2.1.1"
    );
  });

  it("documents v2.1 capabilities and the defensive-only scope", async () => {
    mockSystemInfo();
    render(
      <MemoryRouter initialEntries={["/about"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/about" element={<AboutPage />} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    // The "What v2.1.1 implements today" section header
    // must be present. The About page is the single source
    // of truth for the current milestone and must be
    // kept in sync with ``backend/app/_version.py``.
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /what v2\.1\.1 implements today/i })
      ).toBeInTheDocument();
    });
    // The human-readable evidence report is described
    // with the bounded "not a verdict / not a
    // certification / not a compliance pass-or-fail"
    // wording and the two endpoint paths.
    expect(
      screen.getByText(/human-readable.*evidence report/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /\/api\/v1\/scans\/\{id\}\/reports\/evidence-summary\/preview/
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /\/api\/v1\/scans\/\{id\}\/reports\/evidence-summary\.md/
      )
    ).toBeInTheDocument();
    expect(screen.getByText(/not a security verdict/i)).toBeInTheDocument();
    expect(screen.getByText(/not a certification/i)).toBeInTheDocument();
    expect(
      screen.getByText(/not a compliance pass-or-fail/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not execute analyzed code/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/provider-honesty policy/i)).toBeInTheDocument();
    expect(
      screen.getByText(/authentication, multi-tenancy, billing/i)
    ).toBeInTheDocument();
  });

  it("does not regress to describing v0.1 or v1.0 as the current milestone", async () => {
    mockSystemInfo();
    render(
      <MemoryRouter initialEntries={["/about"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/about" element={<AboutPage />} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    // The legacy "What v0.1 includes" / "What v0.1 does not include"
    // section headers must not be present any more.
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: /what v0\.1 includes/i })
      ).not.toBeInTheDocument();
    });
    expect(
      screen.queryByRole("heading", { name: /what v0\.1 does not include/i })
    ).not.toBeInTheDocument();
    // The stale "What v1.0 implements today" header (the
    // v2.0.6 release replaced the v1.0 about copy with the
    // v2.0.6 copy) must not be present any more either.
    expect(
      screen.queryByRole("heading", { name: /what v1\.0 implements today/i })
    ).not.toBeInTheDocument();
  });
});
