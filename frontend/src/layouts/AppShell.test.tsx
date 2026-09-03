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
  // breakpoint so the inner sticky wrapper sits inside a
  // proper width track and the version / privacy footer can
  // be pinned to the bottom of that wrapper with ``mt-auto``.
  // The mobile ``block`` / ``hidden`` behaviour is preserved
  // unchanged.
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
  // directly under the navigation items. The footer is the
  // last block-level child of the inner sticky flex column
  // (not the outer document-height rail), so ``mt-auto``
  // pushes it to the bottom of the viewport-height wrapper
  // on long pages too instead of to the bottom of the entire
  // document.
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
    // The footer is the last block-level child of the inner
    // sticky flex column so ``mt-auto`` can push it to the
    // bottom of the viewport, not the bottom of the
    // potentially much taller document-height rail.
    const innerColumn = footer?.parentElement;
    expect(innerColumn).not.toBeNull();
    expect(innerColumn?.lastElementChild).toBe(footer);
  });

  // On desktop the sidebar contents must be wrapped in a
  // sticky, viewport-height flex column so the navigation
  // items and the version / privacy footer stay visible
  // even when the main column is taller than the window.
  // Without this, the footer's ``mt-auto`` pushes it to
  // the bottom of the document-height rail and the footer
  // disappears off-screen until the user scrolls.
  it("keeps the desktop sidebar content sticky and viewport-height", () => {
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
    const innerColumn = nav.querySelector("div");
    expect(innerColumn).not.toBeNull();
    // Sticky directly under the 3.5rem (``h-14``) app
    // header so the inner column never slides behind it.
    expect(innerColumn).toHaveClass("lg:sticky");
    expect(innerColumn).toHaveClass("lg:top-14");
    // Viewport-height sizing that matches the content row's
    // own ``min-h-[calc(100vh-3.5rem)]`` so the flex column
    // always fills the available viewport.
    expect(innerColumn).toHaveClass("lg:h-[calc(100vh-3.5rem)]");
    // Flex column so ``mt-auto`` on the footer pins it to
    // the bottom of the inner wrapper. The flex-column base
    // class is always present (it is harmless on mobile
    // where the off-canvas menu simply stacks the items),
    // while the sticky / viewport-height sizing above is
    // desktop-only.
    expect(innerColumn).toHaveClass("flex-col");
    // Safety net for unusually short viewports: scroll
    // inside the sidebar rather than clip. No scrollbar
    // appears when the content fits, so there is no extra
    // scrollbar under ordinary desktop heights.
    expect(innerColumn).toHaveClass("lg:overflow-y-auto");
  });

  // The outer ``<nav>`` rail must remain a full-height flex
  // column on desktop so the sidebar background stretches
  // with long pages instead of revealing the canvas
  // underneath. The sticky inner wrapper is a child of the
  // rail and must not replace it.
  it("keeps the outer sidebar rail as a full-height flex column on desktop", () => {
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
    // The rail itself remains a flex column at the desktop
    // breakpoint so the inner wrapper fills its width.
    expect(nav).toHaveClass("lg:flex");
    expect(nav).toHaveClass("lg:flex-col");
    // The rail still carries the background and right-hand
    // border that span the full document height.
    expect(nav).toHaveClass("bg-surface-sidebar");
    expect(nav).toHaveClass("lg:border-r");
    // The rail itself is not viewport-height: that sizing
    // lives on the inner wrapper so the background can keep
    // growing on long pages.
    expect(nav).not.toHaveClass("h-[calc(100vh-3.5rem)]");
    // The inner sticky wrapper is the only direct child of
    // the rail on desktop, not the navigation list or the
    // footer.
    expect(nav.firstElementChild?.tagName).toBe("DIV");
  });

  // The sticky / viewport-height sidebar treatment is
  // desktop-only. Mobile must keep the original off-canvas
  // block behaviour so the menu can grow to fit all items
  // and the inner wrapper must not be sticky, must not be
  // pinned to the header, and must not be viewport-height.
  it("does not make the mobile sidebar sticky or viewport-height", () => {
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
    const innerColumn = nav.querySelector("div");
    expect(innerColumn).not.toBeNull();
    // No ``sticky``, no ``top-14`` and no viewport-height
    // sizing outside the ``lg:`` prefix. The inner wrapper
    // therefore behaves as a normal flow element on mobile
    // and the off-canvas menu can grow to fit all items.
    expect(innerColumn).not.toHaveClass("sticky");
    expect(innerColumn).not.toHaveClass("top-14");
    expect(innerColumn).not.toHaveClass("h-[calc(100vh-3.5rem)]");
  });
});
