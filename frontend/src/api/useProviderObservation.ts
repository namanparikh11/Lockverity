import { useEffect, useState } from "react";

import { api } from "@/api/api";
import type { ProviderName, ProviderObservation } from "@/api/types";

/**
 * The provider-evidence states a provider-dependent view must
 * distinguish before it may show a clean-style empty result:
 *
 * - ``checked``        — the provider answered (available or
 *                        cached evidence); a "no findings"
 *                        result is justified.
 * - ``unavailable``    — the provider could not be reached.
 * - ``rate_limited``   — the upstream throttled the request.
 * - ``partial``        — only some of the evidence returned.
 * - ``not_requested``  — the provider was not queried for
 *                        this scan (e.g. nothing to query).
 * - ``disabled``       — the operator disabled the provider.
 * - ``not_applicable`` — the provider cannot apply to this
 *                        source (e.g. Scorecard on an archive).
 * - ``unknown``        — no observation data is available.
 */
export type ProviderEvidenceState =
  | "checked"
  | "unavailable"
  | "rate_limited"
  | "partial"
  | "not_requested"
  | "disabled"
  | "not_applicable"
  | "unknown";

/**
 * Reduce one provider's observations for a scan to the single
 * row that represents the truthful aggregate.
 *
 * The rule mirrors the backend aggregation
 * (``app.services.provider_aggregation``): within one logical
 * request (``operation`` + ``component_id``) the latest row
 * wins, so an explicit retry supersedes the earlier attempt;
 * across requests any degraded row (unavailable >
 * rate_limited > partial) wins over the successes, so a later
 * success for another component cannot launder an earlier
 * failure into a clean state. When no row is degraded the
 * reason-bearing ``not_requested`` rows win over the
 * successes so the caller can tell "checked and found
 * nothing" apart from "never fully checked".
 */
export function representativeObservation(
  observations: ProviderObservation[],
): ProviderObservation | null {
  if (observations.length === 0) return null;
  const final = new Map<string, ProviderObservation>();
  for (const obs of observations) {
    final.set(`${obs.operation}|${obs.component_id ?? "scan"}`, obs);
  }
  const rows = [...final.values()];
  const degraded = rows.filter((o) => isDegraded(o.status));
  if (degraded.length > 0) {
    return (
      degraded.find((o) => o.status === "unavailable") ??
      degraded.find((o) => o.status === "rate_limited") ??
      degraded[0]
    );
  }
  const success = rows.filter((o) => o.status === "available" || o.status === "cached");
  if (success.length === rows.length) {
    return rows[rows.length - 1];
  }
  return (
    rows.find((o) => o.error_code === "disabled_by_operator") ??
    rows.find((o) => o.error_code === "not_applicable") ??
    rows.find((o) => o.status === "not_requested") ??
    rows[rows.length - 1]
  );
}

function isDegraded(status: ProviderObservation["status"]): boolean {
  return status === "unavailable" || status === "rate_limited" || status === "partial";
}

/**
 * Map a representative observation to the shared evidence
 * state. The mapping is the single interpretation every
 * provider-dependent view uses; pages must not re-derive it
 * from raw status / error_code pairs.
 */
export function providerEvidenceState(
  observation: ProviderObservation | null,
): ProviderEvidenceState {
  if (!observation) return "unknown";
  switch (observation.status) {
    case "available":
    case "cached":
      return "checked";
    case "unavailable":
      return "unavailable";
    case "rate_limited":
      return "rate_limited";
    case "partial":
      return "partial";
    case "not_requested":
      if (observation.error_code === "disabled_by_operator") return "disabled";
      if (observation.error_code === "not_applicable") return "not_applicable";
      return "not_requested";
    default:
      return "unknown";
  }
}

export function useProviderObservation(
  scanId: number,
  provider: ProviderName,
): ProviderObservation | null {
  const [observation, setObservation] = useState<ProviderObservation | null>(null);

  useEffect(() => {
    if (!Number.isFinite(scanId)) return;
    const controller = new AbortController();
    setObservation(null);
    api
      .listProviderObservations(scanId, {
        page: 1,
        page_size: 200,
        provider,
      })
      .then((response) => {
        if (controller.signal.aborted) return;
        // Aggregate instead of taking the chronologically
        // latest row: a per-component failure followed by an
        // unrelated success must not read as "checked".
        setObservation(representativeObservation(response.items));
      })
      .catch(() => {
        if (!controller.signal.aborted) setObservation(null);
      });
    return () => controller.abort();
  }, [provider, scanId]);

  return observation;
}

export function providerWasDisabledByOperator(
  observation: ProviderObservation | null,
): boolean {
  return (
    observation?.status === "not_requested" &&
    observation.error_code === "disabled_by_operator"
  );
}
