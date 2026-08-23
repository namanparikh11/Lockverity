type Tone = "muted" | "info" | "ok" | "warn" | "danger" | "unknown";

const TONE_CLASSES: Record<Tone, string> = {
  unknown: "bg-ink-100 dark:bg-surface-dark-raised text-ink-700 dark:text-surface-dark-text border-ink-200 dark:border-surface-dark-border",
  info: "bg-accent-50 dark:bg-surface-dark-raised text-accent-700 dark:text-accent-dark-300 border-accent-200",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warn: "bg-amber-50 text-amber-700 border-amber-200",
  danger: "bg-rose-50 text-rose-700 border-rose-200",
  muted: "bg-ink-50 dark:bg-surface-dark-app text-ink-500 dark:text-surface-dark-text-muted border-ink-200 dark:border-surface-dark-border",
};

function toneFor(confidence: string): Tone {
  switch (confidence) {
    case "low":
      return "muted";
    case "medium":
      return "info";
    case "high":
      return "ok";
    case "confirmed":
      return "ok";
    case "unknown":
    default:
      return "unknown";
  }
}

export function ConfidenceBadge({ confidence }: { confidence: string }) {
  const tone = toneFor(confidence);
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
      aria-label={`Confidence: ${confidence}`}
    >
      {confidence}
    </span>
  );
}
