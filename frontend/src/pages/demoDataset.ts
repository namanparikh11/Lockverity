import type { Scan, ScanStatus } from "@/api/types";

/**
 * Explicit seeded-demo identity.
 *
 * A scan is demo data if — and only if — it carries the
 * persisted ``seeded_dataset: "demo"`` provenance marker that
 * the demo dataset loader stamps. Nothing else may be used as
 * demo identity: not the numeric primary key (a real user scan
 * can legitimately occupy id 1, 3, or 4 after migrations,
 * reinstalls, deletions, or ordinary use), not the repository
 * URL, not the status, and not the creation order.
 *
 * The check is applied client-side as defence in depth even
 * though the API request already filters server-side: an older
 * backend that ignores the ``seeded_dataset`` query parameter
 * would return unfiltered scans, and this guard keeps every
 * one of them out of the demo scenarios.
 */
export const DEMO_SEEDED_DATASET = "demo";

export function isDemoScan(scan: Scan): boolean {
  return scan.seeded_dataset === DEMO_SEEDED_DATASET;
}

export interface DemoScenarios {
  completed: Scan | null;
  partial: Scan | null;
  failed: Scan | null;
  cancelled: Scan | null;
}

/**
 * Resolve the demo scenario scans from a scan listing.
 *
 * Only rows carrying the explicit demo marker participate. The
 * first marker-carrying scan per status (lowest id first, so
 * the pick is deterministic regardless of what numeric ids the
 * seed happened to receive) becomes that scenario's entry. The
 * resolved ids — whatever they are — are the only ids the Demo
 * page ever links to.
 */
export function resolveDemoScenarios(scans: Scan[]): DemoScenarios {
  const demoScans = scans.filter(isDemoScan).sort((a, b) => a.id - b.id);
  const pick = (status: ScanStatus): Scan | null =>
    demoScans.find((scan) => scan.status === status) ?? null;
  return {
    completed: pick("completed"),
    partial: pick("partial"),
    failed: pick("failed"),
    cancelled: pick("cancelled"),
  };
}

/** True when at least one marker-carrying demo scan resolved. */
export function hasDemoData(scenarios: DemoScenarios): boolean {
  return (
    scenarios.completed !== null ||
    scenarios.partial !== null ||
    scenarios.failed !== null ||
    scenarios.cancelled !== null
  );
}
