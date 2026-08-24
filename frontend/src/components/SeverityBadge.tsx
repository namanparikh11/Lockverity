import { badgeClasses, type Tone } from "./tone";

function toneFor(severity: string): Tone {
  switch (severity) {
    case "informational":
      return "muted";
    case "low":
      return "info";
    case "medium":
      return "warn";
    case "high":
      return "danger";
    case "critical":
      return "danger";
    case "unknown":
    default:
      return "neutral";
  }
}

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span
      className={badgeClasses(toneFor(severity))}
      aria-label={`Severity: ${severity}`}
    >
      {severity}
    </span>
  );
}
