import { Loader2 } from "lucide-react";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div
      className="flex items-center gap-2 text-sm text-ink-500"
      role="status"
      aria-live="polite"
    >
      <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
      <span>{label}...</span>
    </div>
  );
}
