/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // Custom dark-mode selector. The resolved theme is
  // applied to ``<html data-theme="dark">`` (or
  // ``data-theme="light"``) by the early-paint bootstrap
  // script in ``index.html`` and by the
  // ``AppearanceProvider`` in ``src/appearance/``. The
  // default Tailwind v3 dark-mode strategy is the OS
  // ``prefers-color-scheme`` media query, which does not
  // allow the explicit Light/Dark overrides; the
  // ``selector`` strategy is the only one that satisfies
  // the "System / Light / Dark" appearance model.
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // The ``ink`` and ``accent`` scales are kept
        // exactly as in the Light theme so the
        // canvas/typography math is unchanged. Dark
        // mode remaps ``bg-*`` / ``text-*`` / ``border-*``
        // at the component layer via the semantic
        // utilities in ``index.css``. Existing Light-mode
        // screenshots therefore remain byte-identical.
        ink: {
          50: "#f5f7fa",
          100: "#e4e9f0",
          200: "#c8d1de",
          300: "#9ba8bb",
          400: "#6c7a91",
          500: "#4a5670",
          600: "#384158",
          700: "#2b3245",
          800: "#1c2233",
          900: "#0f1322",
        },
        accent: {
          50: "#eef4ff",
          100: "#dbe6ff",
          200: "#b6ccff",
          300: "#83a8ff",
          400: "#5684f5",
          500: "#3460db",
          600: "#2748b1",
          700: "#1f388a",
          800: "#172a68",
          900: "#0f1c45",
        },
        // Dark-mode accent ramp. Brighter than the
        // Light ramp because the dark canvas needs more
        // luminance to read at the same perceived weight.
        // The 500 step matches the Light ``accent-500``
        // (the Lockverity brand blue) so logo and
        // brand-mark recognition is identical across
        // themes.
        "accent-dark": {
          50: "#0f1c45",
          100: "#172a68",
          200: "#1f388a",
          300: "#2748b1",
          400: "#3460db",
          500: "#3460db",
          600: "#5684f5",
          700: "#83a8ff",
          800: "#b6ccff",
          900: "#dbe6ff",
        },
        // Dark-mode neutrals. Cool dark charcoal
        // background, slightly raised surface, very
        // subtle borders. Sits between slate-900 and
        // zinc-900 to keep a slight blue undertone that
        // does not fight the accent.
        surface: {
          // Light mode (default): kept identical to the
          // previous Light theme so the existing visual
          // design is unchanged.
          light: {
            app: "#f5f7fa", // ink-50
            surface: "#ffffff",
            raised: "#ffffff",
            sidebar: "#ffffff",
            border: "#e4e9f0", // ink-100/200 boundary
            text: "#0f1322", // ink-900
            "text-muted": "#4a5670", // ink-500
            "text-subtle": "#6c7a91", // ink-400
          },
          dark: {
            app: "#0d1117", // app canvas
            surface: "#161b22", // cards
            raised: "#1c2128", // raised surfaces (modals, drawers)
            sidebar: "#0d1117", // matches app, distinct from cards
            border: "#30363d", // subtle separator
            "border-strong": "#484f58",
            text: "#e6edf3", // primary text on dark
            "text-muted": "#8b949e",
            "text-subtle": "#6e7681",
          },
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
