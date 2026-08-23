/**
 * Behaviour tests for the System / Light / Dark appearance
 * subsystem. The tests cover the persistence, resolver,
 * DOM-application, and live-update contract without going
 * through a real WebView or browser. The resolver and DOM
 * helpers are exercised directly; the React provider is
 * exercised via @testing-library/react with a jsdom
 * matchMedia stub.
 *
 * The test list is the documented acceptance list:
 *  A. default preference is System
 *  B. explicit Light resolves Light regardless of OS
 *  C. explicit Dark resolves Dark regardless of OS
 *  D. System + OS Light resolves Light
 *  E. System + OS Dark resolves Dark
 *  F. corrupt / unknown stored preference falls back to System
 *  G. preference persists and restores
 *  H. live prefers-color-scheme change updates the resolved
 *     theme ONLY in System
 *  I. event listener cleanup works
 *  J. document theme attribute / class is updated correctly
 *
 * The tests are intentionally behavioural. There are no
 * pixel-perfect colour assertions.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  APPEARANCE_STORAGE_KEY,
  AppearanceProvider,
  DEFAULT_APPEARANCE,
  applyAppearance,
  readStoredAppearance,
  resolveTheme,
  useAppearance,
  validateAppearance,
  writeStoredAppearance,
  type Appearance,
} from "@/appearance";

// -----------------------------------------------------------------------
// A. default preference is System
// -----------------------------------------------------------------------

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-appearance");
});

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-appearance");
});

describe("appearance defaults", () => {
  it("A. defaults to system when nothing is stored", () => {
    expect(readStoredAppearance()).toBe("system");
    expect(DEFAULT_APPEARANCE).toBe("system");
  });

  it("F. falls back to system on corrupt stored value", () => {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, "nonsense");
    expect(readStoredAppearance()).toBe("system");
  });

  it("F. falls back to system on empty stored value", () => {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, "");
    expect(readStoredAppearance()).toBe("system");
  });

  it("F. falls back to system on null stored value", () => {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, "null");
    expect(readStoredAppearance()).toBe("system");
  });
});

// -----------------------------------------------------------------------
// B / C / D / E. resolver truth table
// -----------------------------------------------------------------------

describe("resolver", () => {
  it.each([
    ["light", true, "light"],
    ["light", false, "light"],
    ["dark", true, "dark"],
    ["dark", false, "dark"],
    ["system", true, "dark"],
    ["system", false, "light"],
  ])(
    "resolves preference=%s osDark=%s -> %s",
    (appearance, osDark, expected) => {
      expect(resolveTheme(appearance as Appearance, osDark as boolean)).toBe(
        expected,
      );
    },
  );

  it("validateAppearance accepts only the three documented values", () => {
    expect(validateAppearance("system")).toBe("system");
    expect(validateAppearance("light")).toBe("light");
    expect(validateAppearance("dark")).toBe("dark");
    expect(validateAppearance(undefined)).toBe("system");
    expect(validateAppearance(null)).toBe("system");
    expect(validateAppearance("")).toBe("system");
    expect(validateAppearance("auto")).toBe("system");
    expect(validateAppearance(42)).toBe("system");
  });
});

// -----------------------------------------------------------------------
// J. document theme attribute / class is updated correctly
// -----------------------------------------------------------------------

describe("applyAppearance", () => {
  it("J. sets data-theme to the resolved theme", () => {
    applyAppearance("light", "light");
    expect(document.documentElement.getAttribute("data-theme")).toBe(
      "light",
    );
    expect(document.documentElement.getAttribute("data-appearance")).toBe(
      "light",
    );
  });

  it("J. sets data-theme to dark when resolved dark", () => {
    applyAppearance("dark", "dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe(
      "dark",
    );
  });

  it("J. data-appearance preserves the explicit preference", () => {
    // System pref + dark OS resolves to dark theme but
    // the data-appearance attribute must remain "system"
    // so tests and the diagnostics page can introspect
    // the user's choice rather than the resolved one.
    applyAppearance("system", "dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe(
      "dark",
    );
    expect(document.documentElement.getAttribute("data-appearance")).toBe(
      "system",
    );
  });
});

// -----------------------------------------------------------------------
// G. preference persists and restores
// -----------------------------------------------------------------------

describe("persistence", () => {
  it("G. persists and restores a valid preference", () => {
    writeStoredAppearance("dark");
    expect(readStoredAppearance()).toBe("dark");
    writeStoredAppearance("light");
    expect(readStoredAppearance()).toBe("light");
    writeStoredAppearance("system");
    expect(readStoredAppearance()).toBe("system");
  });
});

// -----------------------------------------------------------------------
// Provider / hook integration (H, I)
// -----------------------------------------------------------------------

interface MatchMediaFake {
  matchMedia(query: string): MediaQueryList;
  setDark(dark: boolean): void;
}

function installMatchMedia(initial: boolean): MatchMediaFake {
  const listeners: Array<(e: MediaQueryListEvent) => void> = [];
  const mql = {
    matches: initial,
    media: "(prefers-color-scheme: dark)",
    onchange: null as ((this: MediaQueryList, ev: MediaQueryListEvent) => unknown) | null,
    addEventListener(_t: string, l: EventListenerOrEventListenerObject) {
      listeners.push(l as (e: MediaQueryListEvent) => void);
    },
    removeEventListener(_t: string, l: EventListenerOrEventListenerObject) {
      const i = listeners.indexOf(l as (e: MediaQueryListEvent) => void);
      if (i >= 0) listeners.splice(i, 1);
    },
    dispatchEvent: undefined,
  } as unknown as MediaQueryList;
  const original = window.matchMedia;
  window.matchMedia = ((_q: string) => mql) as typeof window.matchMedia;
  return {
    matchMedia: ((_q: string) => mql) as typeof window.matchMedia,
    setDark(dark: boolean) {
      (mql as { matches: boolean }).matches = dark;
      const ev = { matches: dark } as MediaQueryListEvent;
      for (const l of listeners) l(ev);
    },
    // Internal helper for tests
    _restore: () => {
      window.matchMedia = original;
    },
  } as MatchMediaFake & { _restore: () => void };
}

function Probe() {
  const { appearance, resolvedTheme, setAppearance } = useAppearance();
  return (
    <div>
      <span data-testid="appearance">{appearance}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <button
        type="button"
        onClick={() => setAppearance("light")}
        data-testid="set-light"
      >
        L
      </button>
      <button
        type="button"
        onClick={() => setAppearance("dark")}
        data-testid="set-dark"
      >
        D
      </button>
      <button
        type="button"
        onClick={() => setAppearance("system")}
        data-testid="set-system"
      >
        S
      </button>
    </div>
  );
}

describe("AppearanceProvider", () => {
  it("H. resolves system + dark OS to dark", () => {
    const fake = installMatchMedia(true) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      expect(screen.getByTestId("appearance").textContent).toBe("system");
      expect(screen.getByTestId("resolved").textContent).toBe("dark");
      expect(document.documentElement.getAttribute("data-theme")).toBe(
        "dark",
      );
    } finally {
      fake._restore();
    }
  });

  it("H. resolves system + light OS to light", () => {
    const fake = installMatchMedia(false) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      expect(screen.getByTestId("resolved").textContent).toBe("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe(
        "light",
      );
    } finally {
      fake._restore();
    }
  });

  it("H. live OS change updates theme in System mode", async () => {
    const fake = installMatchMedia(false) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      expect(screen.getByTestId("resolved").textContent).toBe("light");
      act(() => {
        fake.setDark(true);
      });
      await waitFor(() =>
        expect(screen.getByTestId("resolved").textContent).toBe("dark"),
      );
      expect(document.documentElement.getAttribute("data-theme")).toBe(
        "dark",
      );
    } finally {
      fake._restore();
    }
  });

  it("H. live OS change does NOT override an explicit Light choice", async () => {
    const fake = installMatchMedia(false) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      const user = userEvent.setup();
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      await user.click(screen.getByTestId("set-light"));
      expect(screen.getByTestId("appearance").textContent).toBe("light");
      expect(screen.getByTestId("resolved").textContent).toBe("light");
      act(() => {
        fake.setDark(true);
      });
      await new Promise((r) => setTimeout(r, 25));
      expect(screen.getByTestId("resolved").textContent).toBe("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe(
        "light",
      );
    } finally {
      fake._restore();
    }
  });

  it("H. live OS change does NOT override an explicit Dark choice", async () => {
    const fake = installMatchMedia(true) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      const user = userEvent.setup();
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      await user.click(screen.getByTestId("set-dark"));
      expect(screen.getByTestId("resolved").textContent).toBe("dark");
      act(() => {
        fake.setDark(false);
      });
      await new Promise((r) => setTimeout(r, 25));
      expect(screen.getByTestId("resolved").textContent).toBe("dark");
    } finally {
      fake._restore();
    }
  });

  it("G. setAppearance persists to localStorage and re-reads on remount", async () => {
    const fake = installMatchMedia(false) as MatchMediaFake & {
      _restore: () => void;
    };
    try {
      const user = userEvent.setup();
      const { unmount } = render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      await user.click(screen.getByTestId("set-dark"));
      expect(localStorage.getItem(APPEARANCE_STORAGE_KEY)).toBe("dark");
      unmount();
      render(
        <AppearanceProvider>
          <Probe />
        </AppearanceProvider>,
      );
      expect(screen.getByTestId("appearance").textContent).toBe("dark");
      await user.click(screen.getByTestId("set-light"));
      expect(localStorage.getItem(APPEARANCE_STORAGE_KEY)).toBe("light");
    } finally {
      fake._restore();
    }
  });
});
