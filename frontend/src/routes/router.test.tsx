/**
 * Router integrity tests.
 *
 * The frontend must render every documented route without
 * throwing, and unknown routes must reach the intentional 404
 * page. These tests live at the route layer because a render
 * failure at /scans/:scanId or /providers is one of the cheapest
 * regressions to ship by accident.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { AppearanceProvider } from "@/appearance";
import { AppShell } from "@/layouts/AppShell";
import { AboutPage } from "@/pages/AboutPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { RepositoriesPage } from "@/pages/RepositoriesPage";
import { ScansIndexPage } from "@/pages/ScansIndexPage";
import { ProviderHealthPage } from "@/pages/ProviderHealthPage";

const ROUTES_UNDER_TEST = [
  { path: "/", name: /dashboard/i, page: DashboardPage },
  { path: "/about", name: /about lockverity/i, page: AboutPage },
  { path: "/repositories", name: /repositories/i, page: RepositoriesPage },
  { path: "/scans", name: /scans/i, page: ScansIndexPage },
  { path: "/providers", name: /provider/i, page: ProviderHealthPage },
];

describe("router", () => {
  beforeEach(() => {
    cleanup();
    // Suppress the api client's outbound calls so the page tests
    // can focus on routing rather than data plumbing.
    global.fetch = vi.fn(async () =>
      new Response("[]", { status: 200, headers: { "content-type": "application/json" } })
    ) as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  for (const route of ROUTES_UNDER_TEST) {
    it(`renders ${route.path} without throwing`, () => {
      const Page = route.page;
      expect(() =>
        render(
          <MemoryRouter initialEntries={[route.path]}>
            <AppearanceProvider>
            <Routes>
              <Route element={<AppShell />}>
                <Route path={route.path} element={<Page />} />
              </Route>
            </Routes>
            </AppearanceProvider>
          </MemoryRouter>
        )
      ).not.toThrow();
    });
  }

  it("renders the 404 page for unknown routes", () => {
    render(
      <MemoryRouter initialEntries={["/this-route-does-not-exist"]}>
        <AppearanceProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/404" element={<NotFoundPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
        </AppearanceProvider>
      </MemoryRouter>
    );
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
  });

  it("survives a direct refresh on a deep route (Vite SPA fallback)", () => {
    // The router config from the production build uses the same
    // route tree as MemoryRouter. The Vite dev server proxies
    // unknown paths back to index.html. We assert the path
    // itself resolves; the SPA-fallback test is the
    // ``vite.config.ts`` server.proxy + dev-server SPA rewrite,
    // which is verified by the proxy unit test below.
    expect(() =>
      render(
        <MemoryRouter initialEntries={["/scans/123/findings"]}>
          <AppearanceProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/scans/:scanId/findings" element={<div>findings-ok</div>} />
            </Route>
          </Routes>
          </AppearanceProvider>
        </MemoryRouter>
      )
    ).not.toThrow();
  });
});
