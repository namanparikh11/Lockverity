/*
 React context + hook for the System / Light / Dark appearance.

 The ``AppearanceProvider`` mounts at the top of the
 React tree (see ``src/main.tsx``). It exposes a
 ``useAppearance()`` hook that returns:

 * ``appearance``: the persisted preference
 (``"system" | "light" | "dark"``);
 * ``resolvedTheme``: the theme currently applied
 to the document (``"light" | "dark"``);
 * ``setAppearance``: a setter that updates the
 preference, persists it, and reapplies the
 resolved theme.

 The provider subscribes to the OS
 ``prefers-color-scheme`` media query when the user
 preference is ``"system"`` so a live OS theme change
 flips the resolved theme without a reload. The
 listener is removed in the cleanup so the component
 is safe to mount once at the app root.

 The early-paint bootstrap in ``index.html``
 mirrors the same logic so the first paint is already
 correct; the provider's initial state is read from
 the same ``localStorage`` key and the same
 ``documentElement`` attribute, so the two
 resolvers agree.
*/

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  APPEARANCE_STORAGE_KEY,
  DEFAULT_APPEARANCE,
  applyAppearance,
  getOsPrefersDark,
  readStoredAppearance,
  resolveTheme,
  validateAppearance,
  writeStoredAppearance,
  type Appearance,
} from "./appearance";
import { AppearanceContext, type AppearanceContextValue } from "./AppearanceContext";

export interface AppearanceProviderProps {
  children: ReactNode;
}

export function AppearanceProvider({ children }: AppearanceProviderProps) {
  // Initial state mirrors the early-paint bootstrap in
  // ``index.html``: read localStorage, validate, and
  // resolve against the current OS preference. We
  // intentionally re-derive on mount rather than
  // trusting the document attribute so tests can
  // exercise the provider with a fresh document.
  const [appearance, setAppearanceState] = useState<Appearance>(() => {
    return readStoredAppearance();
  });
  const [osPrefersDark, setOsPrefersDark] = useState<boolean>(() =>
    getOsPrefersDark(),
  );

  const resolvedTheme = useMemo(
    () => resolveTheme(appearance, osPrefersDark),
    [appearance, osPrefersDark],
  );

  // Apply the resolved theme to the document whenever
  // the preference or the OS preference changes.
  useEffect(() => {
    applyAppearance(appearance, resolvedTheme);
  }, [appearance, resolvedTheme]);

  // Subscribe to the OS ``prefers-color-scheme`` media
  // query so live OS theme changes update the resolved
  // theme in ``system`` mode. The listener is removed
  // in the cleanup so the subscription is bounded to
  // the provider lifetime.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      setOsPrefersDark(event.matches);
    };
    // Initial sync (in case it changed between mount and
    // the effect).
    setOsPrefersDark(media.matches);
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    }
    // Older browsers (incl. some WebView2 builds) only
    // support the legacy ``addListener`` API.
    const legacy = media as unknown as {
      addListener?: (cb: (e: MediaQueryListEvent) => void) => void;
      removeListener?: (cb: (e: MediaQueryListEvent) => void) => void;
    };
    legacy.addListener?.(onChange);
    return () => legacy.removeListener?.(onChange);
  }, []);

  const setAppearance = useCallback((next: Appearance) => {
    const validated = validateAppearance(next);
    writeStoredAppearance(validated);
    setAppearanceState(validated);
  }, []);

  const value = useMemo<AppearanceContextValue>(
    () => ({ appearance, resolvedTheme, setAppearance }),
    [appearance, resolvedTheme, setAppearance],
  );

  return (
    <AppearanceContext.Provider value={value}>
      {children}
    </AppearanceContext.Provider>
  );
}

/** localStorage key, re-exported for tests and the
 *  early-paint bootstrap that mirrors this logic. */
export { APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE };
export type { AppearanceContextValue } from "./AppearanceContext";
