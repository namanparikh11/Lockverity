import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

/**
 * Lockverity notification banner.
 *
 * Used for transient user-facing feedback after a mutation
 * succeeds or fails. Renders inline (does not auto-dismiss) so
 * the user can read it. The notification can be dismissed
 * manually. The tone is always communicated by both an icon and
 * text - colour is decoration, not signal.
 */
type Tone = "info" | "ok" | "warn" | "danger";

const TONE_CLASSES: Record<Tone, string> = {
  info: "border-accent-200 bg-accent-50 dark:bg-surface-dark-raised text-accent-800 dark:text-accent-dark-200",
  ok: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warn: "border-amber-200 bg-amber-50 text-amber-800",
  danger: "border-rose-200 bg-rose-50 text-rose-800",
};

const ICON_CLASSES: Record<Tone, string> = {
  info: "text-accent-600 dark:text-accent-dark-400",
  ok: "text-emerald-600",
  warn: "text-amber-600",
  danger: "text-rose-600",
};

const LABELS: Record<Tone, string> = {
  info: "Info",
  ok: "Success",
  warn: "Warning",
  danger: "Error",
};

export function Notification({
  tone,
  title,
  description,
  onDismiss,
  dismissible = true,
}: {
  tone: Tone;
  title: string;
  description?: string;
  onDismiss?: () => void;
  dismissible?: boolean;
}) {
  return (
    <div
      className={`flex items-start gap-3 rounded-md border p-3 text-sm ${TONE_CLASSES[tone]}`}
      role={tone === "danger" || tone === "warn" ? "alert" : "status"}
      aria-live={tone === "danger" || tone === "warn" ? "assertive" : "polite"}
    >
      <NotificationIcon tone={tone} />
      <div className="flex-1">
        <p className="font-semibold">
          <span className="sr-only">{LABELS[tone]}: </span>
          {title}
        </p>
        {description ? <p className="mt-1">{description}</p> : null}
      </div>
      {dismissible ? (
        <button
          type="button"
          className="rounded p-1 hover:bg-white dark:bg-surface-dark-surface/40"
          onClick={onDismiss}
          aria-label="Dismiss notification"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      ) : null}
    </div>
  );
}

function NotificationIcon({ tone }: { tone: Tone }) {
  const cls = `h-5 w-5 flex-shrink-0 ${ICON_CLASSES[tone]}`;
  if (tone === "ok") return <CheckCircle2 aria-hidden="true" className={cls} />;
  if (tone === "warn") return <AlertTriangle aria-hidden="true" className={cls} />;
  if (tone === "danger") return <XCircle aria-hidden="true" className={cls} />;
  return <Info aria-hidden="true" className={cls} />;
}
