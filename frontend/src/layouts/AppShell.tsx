import {
  Activity,
  Box,
  ClipboardList,
  Info,
  LayoutDashboard,
  Menu,
  PlayCircle,
  ScanSearch,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { api } from "@/api/api";
import { LockveritySymbol } from "@/components/LockveritySymbol";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  end?: boolean;
}

const PRIMARY_NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/analyze", label: "Analyze", icon: Sparkles },
  { to: "/demo", label: "Demo", icon: PlayCircle },
  { to: "/repositories", label: "Repositories", icon: Box },
  { to: "/scans", label: "Scans", icon: ScanSearch },
  { to: "/providers", label: "Providers", icon: ShieldAlert },
  { to: "/diagnostics", label: "Diagnostics", icon: Activity },
  { to: "/about", label: "About", icon: Info },
];

export function AppShell() {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState<string | null>(null);

  // The product version is read from the backend so the UI can
  // never drift from the API surface. The fetch is best-effort:
  // if the backend is unreachable, the footer simply omits the
  // version instead of alarming the user.
  useEffect(() => {
    const controller = new AbortController();
    api
      .systemInfo()
      .then((info) => {
        if (controller.signal.aborted) return;
        setVersion(info.version);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setVersion(null);
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="min-h-screen bg-ink-50 dark:bg-surface-dark-app">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent-600 dark:bg-accent-dark-500 focus:px-3 focus:py-1.5 focus:text-white"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-30 border-b border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface">
        <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-3 px-4">
          <button
            type="button"
            className="rounded p-2 text-ink-700 dark:text-surface-dark-text hover:bg-ink-100 dark:bg-surface-dark-raised lg:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Close navigation" : "Open navigation"}
            aria-expanded={open}
          >
            {open ? (
              <X aria-hidden="true" className="h-5 w-5" />
            ) : (
              <Menu aria-hidden="true" className="h-5 w-5" />
            )}
          </button>
          <a
            href="/"
            className="flex items-center gap-2 font-semibold text-ink-900 dark:text-surface-dark-text"
            data-testid="brand-header-link"
          >
            <LockveritySymbol size={28} decorative className="rounded-md" />
            <span>Lockverity</span>
          </a>
          <span
            className="hidden text-xs text-ink-500 dark:text-surface-dark-text-muted sm:inline"
            aria-hidden="true"
          >
            Evidence-first software supply-chain assurance
          </span>
        </div>
      </header>
      <div className="mx-auto flex max-w-screen-2xl">
        <nav
          aria-label="Primary"
          className={`${
            open ? "block" : "hidden"
          } w-full shrink-0 border-b border-ink-200 dark:border-surface-dark-border bg-white dark:bg-surface-dark-surface lg:block lg:w-60 lg:border-b-0 lg:border-r`}
        >
          <ul className="space-y-1 p-3 text-sm">
            {PRIMARY_NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={"end" in item ? item.end : false}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-2 rounded-md px-3 py-2 ${
                      isActive
                        ? "bg-accent-50 dark:bg-surface-dark-raised text-accent-800 dark:text-accent-dark-200"
                        : "text-ink-700 dark:text-surface-dark-text hover:bg-ink-100 dark:bg-surface-dark-raised"
                    }`
                  }
                >
                  <item.icon aria-hidden="true" className="h-4 w-4" />
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="border-t border-ink-100 dark:border-surface-dark-border p-3 text-xs text-ink-500 dark:text-surface-dark-text-muted">
            <p
              className="flex items-center gap-2 px-3"
              data-testid="brand-footer-version"
            >
              <LockveritySymbol size={16} decorative />
              <ClipboardList aria-hidden="true" className="h-4 w-4" />
              {version ? `v${version}` : "Lockverity"}
            </p>
            <p className="mt-1 px-3 text-ink-400 dark:text-surface-dark-text-subtle">
              Defensive only. Source archives are hostile.
            </p>
            <p className="mt-2 px-3">
              <Link to="/privacy" className="link">
                Privacy policy
              </Link>
            </p>
          </div>
        </nav>
        <main
          id="main-content"
          className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8"
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
