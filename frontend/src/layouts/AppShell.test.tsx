import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/layouts/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

describe("router smoke", () => {
  it("renders the dashboard at /", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    // The dashboard renders its PageHeader; the title text is
    // present in the AppShell as well, so we look for the
    // PageHeader's "Dashboard" heading specifically.
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("renders the 404 page for unknown routes", () => {
    render(
      <MemoryRouter initialEntries={["/this-route-does-not-exist"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/404" element={<NotFoundPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
  });
});

describe("AppShell layout", () => {
  // The shared shell must establish a full-viewport flex column
  // so the header pins to the top and the content row below
  // fills the remaining window. Without this, short routes such
  // as Providers, About and Demo leave the navigation rail
  // visually terminating above the bottom of the viewport.
  it("uses a full-viewport flex column shell", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const shell = container.firstChild as HTMLElement;
    expect(shell).toHaveClass("flex");
    expect(shell).toHaveClass("flex-col");
    expect(shell).toHaveClass("min-h-screen");
  });

  // The content row must have a viewport-aware minimum height
  // that accounts for the sticky header (``h-14`` = 3.5rem) and
  // grows naturally with content on long pages so page-level
  // scrolling is preserved.
  it("gives the content row a viewport-aware minimum height", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const shell = container.firstChild as HTMLElement;
    const row = shell.querySelector(":scope > div") as HTMLElement;
    expect(row).toHaveClass("min-h-[calc(100vh-3.5rem)]");
    expect(row).toHaveClass("flex-1");
  });

  // The sidebar must lay out as a flex column at the desktop
  // breakpoint so the version / privacy footer can be pinned to
  // the bottom of the rail with ``mt-auto``. The mobile
  // ``block`` / ``hidden`` behaviour is preserved unchanged.
  it("lays the sidebar out as a flex column on desktop", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav).toHaveClass("lg:flex");
    expect(nav).toHaveClass("lg:flex-col");
  });

  // The sidebar footer must use ``mt-auto`` so it pins to the
  // bottom of the rail on short pages instead of floating
  // directly under the navigation items.
  it("pins the sidebar footer to the bottom of the rail", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const privacyLink = screen.getByRole("link", { name: /privacy policy/i });
    const footer = privacyLink.closest("div");
    expect(footer).not.toBeNull();
    expect(footer).toHaveClass("mt-auto");
    // The footer is the last block-level child of the nav so
    // ``mt-auto`` can push it to the bottom of the flex column.
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav.lastElementChild).toBe(footer);
  });
});
