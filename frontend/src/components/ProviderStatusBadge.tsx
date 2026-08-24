import { badgeClasses, type Tone } from "./tone";

/**
 * Provider availability chip. ``available`` is success,
 * ``unavailable`` is danger, ``partial`` / ``rate_limited`` are
 * warnings, and everything the backend did not assert stays
 * neutral. The meaning is identical in both themes; only the tint
 * and foreground adapt (see ``./tone``).
 */

function toneFor(status: string): Tone {
  switch (status) {
    case "available":
    case "cached":
      return "ok";
    case "partial":
      return "warn";
    case "rate_limited":
      return "warn";
    case "unavailable":
      return "danger";
    case "not_requested":
    case "unknown":
      return "muted";
    default:
      return "neutral";
  }
}

export function ProviderStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={badgeClasses(toneFor(status))}
      aria-label={`Provider status: ${status}`}
    >
      {status}
    </span>
  );
}
