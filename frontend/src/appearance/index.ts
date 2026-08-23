/*
 Public entry point for the appearance subsystem.

 Re-exports the public surface of
 ``src/appearance/``. Keeping the import path
 centralised means consumers (the React hook, the
 About page, tests) all import from the same place
 and the surface is easy to audit.
*/

export {
  APPEARANCE_STORAGE_KEY,
  DEFAULT_APPEARANCE,
  applyAppearance,
  getOsPrefersDark,
  readStoredAppearance,
  resolveTheme,
  validateAppearance,
  writeStoredAppearance,
} from "./appearance";
export type { Appearance, ResolvedTheme } from "./appearance";

export { AppearanceProvider } from "./AppearanceProvider";
export { useAppearance } from "./useAppearance";
export type { AppearanceContextValue } from "./AppearanceContext";

export { AppearanceControl } from "./AppearanceControl";
