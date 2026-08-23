/**
 * ``useAppearance`` hook, isolated from the provider so
 * the provider file only exports components (this is
 * what the react-refresh ESLint rule checks for fast
 * refresh to work in dev). The provider and the hook
 * are exported together from ``./index.ts``.
 */
import { useContext } from "react";

import { AppearanceContext, type AppearanceContextValue } from "./AppearanceContext";

export function useAppearance(): AppearanceContextValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) {
    throw new Error(
      "useAppearance() must be used inside <AppearanceProvider>",
    );
  }
  return ctx;
}
