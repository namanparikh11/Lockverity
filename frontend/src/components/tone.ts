/*
 Shared semantic tone vocabulary for Lockverity status chips.

 ``StatusBadge``, ``ProviderStatusBadge``, ``SeverityBadge`` and
 ``ConfidenceBadge`` each carried a byte-identical copy of this
 table. Four copies meant four places to forget when the theme
 changed, which is how the Dark build ended up with chips whose
 tint, foreground and border disagreed. The vocabulary lives
 here now; the badges only map their own domain word onto a
 tone.

 The class strings are deliberately written against the shared
 ``ink`` / ``accent`` / ``emerald`` / ``amber`` / ``rose`` ramps
 rather than against literal colours. Those ramps are
 theme-aware (see ``src/index.css``), so one chip definition
 renders a pale tint with a dark foreground in Light and a muted
 tint with a bright foreground in Dark. Semantics never change
 between themes: available stays success, unavailable stays
 danger, partial stays warning.
*/

export type Tone = "neutral" | "info" | "ok" | "warn" | "danger" | "muted";

/** Tint + foreground + border for each tone. */
export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-ink-100 text-ink-700 border-ink-200",
  info: "bg-accent-50 text-accent-700 border-accent-200",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warn: "bg-amber-50 text-amber-700 border-amber-200",
  danger: "bg-rose-50 text-rose-700 border-rose-200",
  muted: "bg-ink-50 text-ink-500 border-ink-200",
};

/** The chip geometry every badge shares. */
export const BADGE_BASE =
  "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium";

/** Class list for a chip of the given tone. */
export function badgeClasses(tone: Tone): string {
  return `${BADGE_BASE} ${TONE_CLASSES[tone]}`;
}
