/**
 * Shared React context for the appearance subsystem.
 * Defined in its own file so the provider component file
 * can export only components (react-refresh friendly).
 */
import { createContext } from "react";

import type { Appearance, ResolvedTheme } from "./appearance";

export interface AppearanceContextValue {
  appearance: Appearance;
  resolvedTheme: ResolvedTheme;
  /** Set the persisted preference and reapply the theme. */
  setAppearance: (next: Appearance) => void;
}

export const AppearanceContext = createContext<AppearanceContextValue | null>(
  null,
);
