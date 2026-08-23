import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "@/api/api";
import { ApiClientError, categorizeError } from "@/api/client";
import {
  defaultExternalEvidenceProviderSelection,
  providerSelectionPayload,
} from "@/api/providerSelection";
import { TERMINAL_SCAN_STATUSES } from "@/api/scanPolling";
import type {
  ExternalEvidenceProviderSelection,
  RepositorySourceType,
  Scan,
} from "@/api/types";
import { ConfirmationDialog } from "@/components/ConfirmationDialog";
import { DataCompletenessNotice } from "@/components/DataCompletenessNotice";
import { ErrorState } from "@/components/ErrorState";
import { ExternalEvidenceProviderSelector } from "@/components/ExternalEvidenceProviderSelector";

/**
 * v1.6 scan execution controls.
 *
 * The component is rendered next to the scan header
 * summary. It surfaces the truthful next action for the
 * scan's current persisted status:
 *
 * - queued: ``Start scan`` (calls ``/scans/{id}/run``)
 * - queued/running: ``Cancel scan`` (with confirmation)
 * - completed/partial: ``Run another scan`` creates a
 *   new queued scan for the same repository, starts it,
 *   and navigates to the new workbench.
 * - failed/cancelled: ``Retry as new scan`` with the
 *   same semantics.
 *
 * The historical scan is never mutated; the v0.5+
 * terminal-state rule is honoured by always creating a
 * new scan. The component never claims the scan is
 * secure, clean, or certified.
 */
export function ScanActions({
  scan,
  refreshKey,
}: {
  scan: Scan;
  /**
   * Any value that changes when the parent wants the
   * actions to re-fetch the scan (e.g. after a
   * cancel or start). The current scan object is
   * always read from props so the parent can keep the
   * polled value authoritative.
   */
  refreshKey?: number;
}) {
  const navigate = useNavigate();
  // The action error is paired with the pending
  // action that produced it. Storing both together
  // ensures the bounded error title survives the
  // ``finally`` block that resets ``pending`` to
  // null after the action settles.
  const [actionError, setActionError] = useState<
    | { error: unknown; action: "start" | "cancel" | "rescan" }
    | null
  >(null);
  const [pending, setPending] = useState<null | "start" | "cancel" | "rescan">(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [lastRescan, setLastRescan] = useState<{ scan_id: number } | null>(null);
  const [repositorySourceType, setRepositorySourceType] =
    useState<RepositorySourceType | null>(null);
  const [providerSelection, setProviderSelection] =
    useState<ExternalEvidenceProviderSelection>(
      defaultExternalEvidenceProviderSelection
    );
  // Synchronous guard for rapid duplicate clicks.
  // The ``pending`` state is React-asynchronous, so
  // two synchronous clicks can both pass the
  // ``if (pending) return`` check before the state
  // propagates. The ref check is synchronous and
  // blocks the second call immediately.
  const pendingRef = useRef(false);

  // Reset any stale action error / pending state when
  // the parent refetches the scan.
  useEffect(() => {
    setActionError(null);
  }, [refreshKey]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getRepository(scan.repository_id)
      .then((repository) => {
        if (!controller.signal.aborted) setRepositorySourceType(repository.source_type);
      })
      .catch(() => {
        if (!controller.signal.aborted) setRepositorySourceType(null);
      });
    return () => controller.abort();
  }, [scan.repository_id]);

  const actionErrorAction = actionError?.action ?? pending;

  const isTerminal = TERMINAL_SCAN_STATUSES.has(scan.status);

  async function startScan() {
    if (pending || pendingRef.current) return;
    pendingRef.current = true;
    setActionError(null);
    setPending("start");
    try {
      await api.runScan(scan.id, providerSelectionPayload(providerSelection));
    } catch (err) {
      setActionError({ error: err, action: "start" });
    } finally {
      setPending(null);
      pendingRef.current = false;
    }
  }

  async function cancelScan() {
    if (pending || pendingRef.current) return;
    pendingRef.current = true;
    setActionError(null);
    setPending("cancel");
    try {
      await api.cancelScan(scan.id, { reason: "operator_cancelled_via_ui" });
    } catch (err) {
      setActionError({ error: err, action: "cancel" });
    } finally {
      setPending(null);
      pendingRef.current = false;
      setConfirmCancel(false);
    }
  }

  async function rescan() {
    if (pending || pendingRef.current) return;
    pendingRef.current = true;
    setActionError(null);
    setPending("rescan");
    try {
      // v1.6: retry/rescan always creates a new queued
      // scan for the same repository. The historical
      // scan is never mutated; the v0.5+ terminal-state
      // rule is preserved by design.
      const newScan = await api.rescanRepository(scan.repository_id, {
        trigger_type: scan.trigger_type,
        requested_ref: scan.requested_ref ?? undefined,
      });
      setLastRescan({ scan_id: newScan.id });
      // Schedule the new scan on the local worker.
      // The result is best-effort: the page navigates
      // to the new workbench regardless, where the
      // reviewer can retry-start if needed.
      try {
        await api.runScan(newScan.id, providerSelectionPayload(providerSelection));
      } catch (err) {
        // Partial success: the new scan exists but the
        // worker did not start it. The new workbench
        // surfaces a retry-start button.
        setActionError({ error: err, action: "rescan" });
      }
      navigate(`/scans/${newScan.id}`);
    } catch (err) {
      setActionError({ error: err, action: "rescan" });
    } finally {
      setPending(null);
      pendingRef.current = false;
    }
  }

  return (
    <div className="space-y-2">
      {scan.status === "queued" || isTerminal ? (
        <ExternalEvidenceProviderSelector
          idPrefix={`scan-${scan.id}-provider`}
          value={providerSelection}
          onChange={setProviderSelection}
          openssfApplicable={repositorySourceType !== "uploaded_archive"}
          disabled={pending !== null}
        />
      ) : null}
      {isTerminal && repositorySourceType === "github" ? (
        <DataCompletenessNotice
          title="GitHub retrieval on rescan"
          description="A rescan of this GitHub repository contacts GitHub to resolve and download the repository before analysis. This required retrieval is separate from the optional evidence providers above."
          tone="muted"
        />
      ) : null}
      <div className="flex flex-wrap gap-2" data-testid="scan-actions">
        <ActionButton
          label="Start scan"
          onClick={startScan}
          disabled={pending !== null || scan.status !== "queued"}
          busy={pending === "start"}
          variant="primary"
          testId="scan-action-start"
        />
        <ActionButton
          label="Cancel scan"
          onClick={() => setConfirmCancel(true)}
          disabled={pending !== null || isTerminal}
          busy={pending === "cancel"}
          variant="danger"
          testId="scan-action-cancel"
        />
        {isTerminal ? (
          <ActionButton
            label={
              scan.status === "failed" || scan.status === "cancelled"
                ? "Retry as new scan"
                : "Run another scan"
            }
            onClick={rescan}
            disabled={pending !== null}
            busy={pending === "rescan"}
            variant="secondary"
            testId="scan-action-rescan"
          />
        ) : null}
        <Link
          to={`/scans/${scan.id}/findings`}
          className="btn-secondary"
          data-testid="scan-action-findings"
        >
          View findings
        </Link>
        <Link
          to={`/scans/${scan.id}/exports`}
          className="btn-secondary"
          data-testid="scan-action-exports"
        >
          Exports
        </Link>
      </div>
      {actionError ? (
        <div role="alert" data-testid="scan-action-error">
          {actionError.error instanceof ApiClientError ? (
            <ErrorState
              error={actionError.error}
              title={actionErrorTitleFor(
                categorizeError(actionError.error),
                actionErrorAction
              )}
            />
          ) : (
            <ErrorState
              error={actionError.error}
              title={
                actionErrorAction
                  ? `Could not ${actionErrorAction} the scan`
                  : "Could not perform the action"
              }
            />
          )}
        </div>
      ) : null}
      {lastRescan ? (
        <p className="text-xs text-ink-500 dark:text-surface-dark-text-muted" role="status">
          Latest rescan: scan #{lastRescan.scan_id}
        </p>
      ) : null}
      <ConfirmationDialog
        open={confirmCancel}
        title={`Cancel scan #${scan.id}?`}
        description="Persisted evidence remains available, but unfinished stages will not complete."
        confirmLabel="Cancel scan"
        destructive
        busy={pending === "cancel"}
        onConfirm={cancelScan}
        onCancel={() => setConfirmCancel(false)}
      />
    </div>
  );
}

function ActionButton({
  label,
  onClick,
  disabled,
  busy,
  variant,
  testId,
}: {
  label: string;
  onClick: () => void;
  disabled: boolean;
  busy: boolean;
  variant: "primary" | "secondary" | "danger";
  testId: string;
}) {
  const className =
    variant === "primary"
      ? "btn-primary"
      : variant === "danger"
        ? "btn bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50"
        : "btn-secondary";
  return (
    <button
      type="button"
      className={className}
      onClick={onClick}
      disabled={disabled || busy}
      data-testid={testId}
    >
      {busy ? `${label}…` : label}
    </button>
  );
}

function actionErrorTitleFor(
  category: ReturnType<typeof categorizeError>,
  pending: null | "start" | "cancel" | "rescan"
): string {
  if (pending === "start") {
    if (category === "validation")
      return "Scan cannot be started from its current state";
    if (category === "internal_unexpected")
      return "An internal error occurred";
    return "Could not start the scan";
  }
  if (pending === "cancel") {
    if (category === "validation")
      return "Scan cannot be cancelled from its current state";
    return "Could not cancel the scan";
  }
  if (pending === "rescan") {
    if (category === "rescan_source_unavailable") {
      return "Rescan source is no longer available";
    }
    if (category === "validation") return "Scan request rejected by the server";
    return "Could not create a new scan";
  }
  // v2.1.1: the catch-all no longer claims
  // ``Unknown error`` when the backend has already
  // supplied a classified message. The body's
  // ``describeError`` call still renders the safe
  // backend message; the title is a generic catch-all.
  return "Could not perform the action";
}
