import { Check, Copy } from "lucide-react";
import { useState } from "react";

export function CopyableIdentifier({
  value,
  label,
  className = "",
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      {label ? <span className="text-ink-500">{label}:</span> : null}
      <code className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-xs text-ink-700">
        {value}
      </code>
      <button
        type="button"
        className="rounded p-1 text-ink-400 hover:bg-ink-100 hover:text-ink-700"
        onClick={handleCopy}
        aria-label={copied ? "Copied" : "Copy identifier"}
      >
        {copied ? (
          <Check aria-hidden="true" className="h-3.5 w-3.5" />
        ) : (
          <Copy aria-hidden="true" className="h-3.5 w-3.5" />
        )}
      </button>
    </span>
  );
}
