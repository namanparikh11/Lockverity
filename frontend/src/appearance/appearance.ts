/*
 Appearance preference model and DOM-application helpers.

 This module is the single source of truth for the
 "System / Light / Dark" appearance contract.

 * ``Appearance`` is the user preference. It is
 persisted to ``localStorage`` under
 ``lockverity.appearance``. Values other than
 ``"system" | "light" | "dark"`` are treated as
 ``"system"`` (the safe default for new users and
 for any corrupted legacy value).

 * ``ResolvedTheme`` is the theme that is actually
 applied to the document. It is ``"light"`` or
 ``"dark"``. The resolver combines the stored
 preference with the OS-level
 ``prefers-color-scheme`` media query; an explicit
 Light or Dark preference always wins over the OS.

 * The ``applyAppearance`` function is the only
 place that mutates the document. It sets both
 ``data-theme`` (for CSS) and ``data-appearance``
 (the persisted preference, used for diagnostics
 and tests).

 The module is framework-agnostic: it can be called
 from the React hook, the early-paint bootstrap in
 ``index.html`` (which mirrors this logic so the
 first paint is correct), and unit tests. No React,
 no router, no router context.
*/

export type Appearance = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

/** localStorage key for the persisted preference. */
export const APPEARANCE_STORAGE_KEY = "lockverity.appearance";

/** Default preference for new users. */
export const DEFAULT_APPEARANCE: Appearance = "system";

/** Set of valid preference values, used for validation. */
const VALID_APPEARANCES: ReadonlySet<Appearance> = new Set([
  "system",
  "light",
  "dark",
]);

/**
 * Validate a stored preference value.
 *
 * Returns ``"system"`` for any value that is not one of
 * the three documented preferences. This includes
 * ``null``, ``undefined``, empty strings, and any
 * legacy / corrupted value.
 */
export function validateAppearance(value: unknown): Appearance {
  if (typeof value !== "string") return DEFAULT_APPEARANCE;
  if (VALID_APPEARANCES.has(value as Appearance)) return value as Appearance;
  return DEFAULT_APPEARANCE;
}

/**
 * Read the OS preference. The function is
 * best-effort: in environments without
 * ``window.matchMedia`` (very old WebViews, certain
 * test setups) it returns ``false``.
 */
export function getOsPrefersDark(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/**
 * Resolve a stored preference against the OS
 * preference. The rule is:
 *
 *   preference === "dark"  -> "dark"
 *   preference === "light" -> "light"
 *   preference === "system" -> osPrefersDark ? "dark" : "light"
 */
export function resolveTheme(
  appearance: Appearance,
  osPrefersDark: boolean,
): ResolvedTheme {
  if (appearance === "dark") return "dark";
  if (appearance === "light") return "light";
  return osPrefersDark ? "dark" : "light";
}

/**
 * Apply the resolved theme to the document. The
 * function sets both ``data-theme`` and
 * ``data-appearance`` on ``<html>``. It is
 * safe to call repeatedly: the document is the
 * single source of truth and React components are
 * expected to re-render when the appearance context
 * changes.
 *
 * The function never throws and never touches
 * localStorage. The caller is responsible for
 * persisting the preference.
 */
export function applyAppearance(
  appearance: Appearance,
  resolved: ResolvedTheme,
  root: HTMLElement = typeof document !== "undefined"
    ? document.documentElement
    : (undefined as unknown as HTMLElement),
): void {
  if (!root) return;
  try {
    root.setAttribute("data-theme", resolved);
    root.setAttribute("data-appearance", appearance);
  } catch {
    // The DOM is the only place that can throw; if it
    // does (very rare, e.g. a test environment without
    // a real document), fall back to a no-op.
  }
}

/**
 * Read the persisted appearance from
 * localStorage. Returns the validated value. If the
 * stored value is missing or corrupted, the
 * validated default is returned.
 *
 * The function is best-effort: if localStorage is
 * unavailable (private-mode, sandboxed WebView,
 * very restrictive CSP), it returns the default
 * without throwing.
 */
export function readStoredAppearance(): Appearance {
  if (typeof window === "undefined") return DEFAULT_APPEARANCE;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
  } catch {
    return DEFAULT_APPEARANCE;
  }
  return validateAppearance(raw);
}

/**
 * Persist the preference. The function is
 * best-effort and never throws.
 */
export function writeStoredAppearance(appearance: Appearance): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, appearance);
  } catch {
    // localStorage may be unavailable; the in-memory
    // state still works for the current session.
  }
}
