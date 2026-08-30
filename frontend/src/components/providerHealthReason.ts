/**
 * Provider health reason presentation helper.
 *
 * The per-provider rollup card and the per-scan observation row
 * both surface a free-form ``redacted_failure_summary`` /
 * ``error_summary`` string. When the underlying provider failure
 * is a network call, that string can include the raw HTTP method
 * and endpoint URL (for example
 * ``HTTP GET https://api.securityscorecards.dev/... failed ...``).
 * That text is technically accurate but looks like a backend
 * log message leaking into the normal product UI.
 *
 * The backend already exposes enough structured information to
 * derive a clean, human-readable reason: the ``status`` enum,
 * the ``last_error_code`` / ``error_code`` taxonomy, and (for
 * per-scan observations) the ``http_status`` integer. This
 * helper uses those structured fields and falls back to a
 * generic phrase only when no structured signal is available.
 *
 * Raw technical evidence (HTTP method, endpoint, full
 * exception string, error code) is preserved on the underlying
 * data structure and remains available in:
 *
 *  - the Diagnostics page (structured provider table)
 *  - the application logs
 *  - the underlying API payloads
 *
 * It is deliberately NOT re-surfaced on normal pages (no
 * tooltip / title attribute and no raw-summary fallback text);
 * the normal Provider Health UI exposes only the concise
 * structured reason derived here.
 *
 * Nothing is dropped from the API contract; this helper is a
 * pure presentation layer.
 */

import type { ProviderHealthEntry, ProviderObservation, ProviderStatus } from "@/api/types";

/**
 * The minimum structured surface needed to derive a concise
 * provider reason. Both the rollup entry and the per-scan
 * observation can be projected onto this shape, which keeps
 * the helper focused on the data it actually consumes.
 */
export interface ProviderFailureContext {
  status: ProviderStatus;
  errorCode: string | null;
  httpStatus: number | null;
}

/**
 * Codes that mean "the provider was deliberately not queried"
 * or "there was nothing to query against". The status badge
 * already communicates the absence; we do not add a separate
 * reason line for these. ``disabled_by_operator`` and
 * ``not_applicable`` are handled separately above the
 * early-return so the operator-facing and applicability
 * phrases are still surfaced even when the status badge says
 * ``not_requested``.
 */
const SKIP_CODES: ReadonlySet<string> = new Set([
  "no_components",
  "no_workflow_files",
]);

/**
 * Extract a numeric HTTP status from a structured
 * ``last_error_code`` / ``error_code`` such as ``http_503`` or
 * ``http_429``. Returns ``null`` when the code does not carry
 * an HTTP status - never guesses from free-form text.
 */
export function extractHttpStatusFromErrorCode(errorCode: string | null): number | null {
  if (!errorCode) return null;
  // Only the well-known ``http_<digits>`` taxonomy is honoured.
  // The backend uses this exact shape (see
  // ``backend/app/providers/*.py``); the test suite
  // (``test_api_v0_2_endpoints.py``) asserts it.
  const match = /^http_(\d{3})$/.exec(errorCode);
  if (!match) return null;
  const n = Number.parseInt(match[1], 10);
  if (!Number.isFinite(n) || n < 100 || n > 599) return null;
  return n;
}

/**
 * Map a structured HTTP status to a category phrase. The
 * branches here mirror the small set of phrases the product
 * UI is allowed to show on a Provider Health card.
 */
function phraseForHttpStatus(status: number): string {
  if (status === 401 || status === 403) {
    return `Authentication failed · HTTP ${status}`;
  }
  if (status === 429) {
    return `Rate limited · HTTP ${status}`;
  }
  return `Request failed · HTTP ${status}`;
}

/**
 * Derive a concise, human-readable reason for a provider
 * failure. Returns ``null`` when the structured state is
 * already self-explanatory (available / cached / not
 * requested / unknown / skip-codes) and there is no need to
 * add a separate reason line below the status badge.
 *
 * The helper never invents a status code. The HTTP status
 * shown in the phrase is always derived from
 * :field:`httpStatus` or from a recognised
 * ``http_<digits>`` :field:`errorCode`. When neither is
 * available, the helper falls back to a category phrase
 * (``Provider unavailable`` / ``Provider request failed``).
 */
export function providerReason(ctx: ProviderFailureContext): string | null {
  // Operator-disabled is honoured even when the status is
  // ``not_requested`` - the historical card showed a separate
  // "Disabled by operator" line in that case, and the badge
  // alone ("not requested") would otherwise be ambiguous.
  if (ctx.errorCode === "disabled_by_operator") {
    return "Disabled by operator";
  }

  // ``not_applicable`` keeps its explicit user-facing semantic
  // ("this provider cannot apply to this source"), distinct
  // from not-requested, from failure, and from clean success.
  // Like the disabled case it is derived purely from the
  // structured ``error_code`` taxonomy, never from free-form
  // summary text.
  if (ctx.errorCode === "not_applicable") {
    return "Not applicable";
  }

  if (
    ctx.status === "available" ||
    ctx.status === "cached" ||
    ctx.status === "not_requested" ||
    ctx.status === "unknown"
  ) {
    return null;
  }

  if (ctx.errorCode !== null && SKIP_CODES.has(ctx.errorCode)) {
    return null;
  }

  // Rate-limit semantics take priority over a generic
  // request-failed phrase, because the operator-facing meaning
  // is distinct: the upstream is throttling us, not failing.
  // The HTTP status can come from either the structured
  // ``httpStatus`` field (per-scan observations) or from a
  // recognised ``http_<digits>`` error code (the rollup
  // surface).
  if (ctx.errorCode === "rate_limited" || ctx.status === "rate_limited") {
    const code = ctx.httpStatus ?? extractHttpStatusFromErrorCode(ctx.errorCode);
    if (code !== null) return `Rate limited · HTTP ${code}`;
    return "Rate limited";
  }

  if (ctx.httpStatus !== null) {
    return phraseForHttpStatus(ctx.httpStatus);
  }

  const codeFromErrorCode = extractHttpStatusFromErrorCode(ctx.errorCode);
  if (codeFromErrorCode !== null) {
    return phraseForHttpStatus(codeFromErrorCode);
  }

  if (ctx.status === "unavailable") {
    // An ``unavailable`` status with an unrecognised code still
    // gets the same generic phrase - we never fabricate a
    // cause that the structured data does not carry.
    return "Provider unavailable";
  }

  // ``partial`` and any other failure mode: a single neutral
  // phrase that does not leak the raw provider exception.
  return "Provider request failed";
}

/**
 * Adapter for the per-provider rollup card. The rollup does
 * not carry an ``http_status`` field; we derive the HTTP
 * status from ``last_error_code`` when it is present.
 */
export function providerHealthEntryReason(entry: ProviderHealthEntry): string | null {
  return providerReason({
    status: entry.status,
    errorCode: entry.last_error_code,
    httpStatus: extractHttpStatusFromErrorCode(entry.last_error_code),
  });
}

/**
 * Adapter for the per-scan observation row. The observation
 * carries a first-class ``http_status`` integer plus the
 * ``error_code`` taxonomy, so both signals can be used.
 */
export function providerObservationReason(observation: ProviderObservation): string | null {
  return providerReason({
    status: observation.status,
    errorCode: observation.error_code,
    httpStatus: observation.http_status,
  });
}
