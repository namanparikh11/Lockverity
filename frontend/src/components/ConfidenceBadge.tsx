import { badgeClasses, type Tone } from "./tone";

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
      return "neutral";
  }
}

export function ConfidenceBadge({ confidence }: { confidence: string }) {
  return (
    <span
      className={badgeClasses(toneFor(confidence))}
      aria-label={`Confidence: ${confidence}`}
    >
      {confidence}
    </span>
  );
}
