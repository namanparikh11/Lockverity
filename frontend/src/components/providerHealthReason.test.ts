import { describe, expect, it } from "vitest";

import type { ProviderHealthEntry, ProviderObservation, ProviderStatus } from "@/api/types";
import {
  extractHttpStatusFromErrorCode,
  providerHealthEntryReason,
  providerObservationReason,
  providerReason,
} from "@/components/providerHealthReason";

/**
 * Focused tests for the provider-health reason helper.
 *
 * These tests assert the single, narrow contract the
 * presentation layer must hold on the Provider Health page:
 *
 *  1. raw URL-bearing provider failures are never shown
 *     verbatim on Provider Health (the structured state
 *     drives the phrase instead);
 *  2. generic request failures render a concise
 *     human-readable reason;
 *  3. HTTP status renders when genuinely available;
 *  4. rate-limit semantics remain distinguishable;
 *  5. not_requested / disabled / available states remain
 *     unchanged (the helper returns null and the page
 *     therefore omits the reason line);
 *  6. technical / raw evidence is not deleted from the
 *     underlying data structure - we only assert the helper
 *     does not depend on it.
 */

function entry(overrides: Partial<ProviderHealthEntry> = {}): ProviderHealthEntry {
  return {
    provider: "openssf",
    status: "unavailable" as ProviderStatus,
    last_retrieved_at: null,
    records_returned: 0,
    cache_status: null,
    redacted_failure_summary: null,
    last_error_code: null,
    scans_with_observations: 1,
    ...overrides,
  };
}

function observation(
  overrides: Partial<ProviderObservation> = {}
): ProviderObservation {
  return {
    id: 1,
    scan_run_id: 1,
    provider: "openssf",
    operation: "scorecard_lookup",
    status: "unavailable" as ProviderStatus,
    requested_at: null,
    completed_at: null,
    http_status: null,
    records_returned: 0,
    cache_status: null,
    retry_after: null,
    error_code: null,
    error_summary: null,
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:00:00Z",
    ...overrides,
  };
}

describe("extractHttpStatusFromErrorCode", () => {
  it("parses the http_<digits> taxonomy", () => {
    expect(extractHttpStatusFromErrorCode("http_503")).toBe(503);
    expect(extractHttpStatusFromErrorCode("http_429")).toBe(429);
    expect(extractHttpStatusFromErrorCode("http_401")).toBe(401);
  });

  it("returns null for codes that do not carry an HTTP status", () => {
    expect(extractHttpStatusFromErrorCode("provider_unavailable")).toBeNull();
    expect(extractHttpStatusFromErrorCode("rate_limited")).toBeNull();
    expect(extractHttpStatusFromErrorCode("upstream_5xx")).toBeNull();
    expect(extractHttpStatusFromErrorCode("disabled_by_operator")).toBeNull();
    expect(extractHttpStatusFromErrorCode("")).toBeNull();
    expect(extractHttpStatusFromErrorCode(null)).toBeNull();
  });

  it("rejects out-of-range and non-three-digit codes", () => {
    expect(extractHttpStatusFromErrorCode("http_99")).toBeNull();
    expect(extractHttpStatusFromErrorCode("http_700")).toBeNull();
    expect(extractHttpStatusFromErrorCode("http_abc")).toBeNull();
    expect(extractHttpStatusFromErrorCode("http_53")).toBeNull();
  });
});

describe("providerReason", () => {
  it("returns null for self-explanatory success / neutral states", () => {
    expect(
      providerReason({ status: "available", errorCode: null, httpStatus: null })
    ).toBeNull();
    expect(
      providerReason({ status: "cached", errorCode: null, httpStatus: null })
    ).toBeNull();
    expect(
      providerReason({ status: "not_requested", errorCode: null, httpStatus: null })
    ).toBeNull();
    expect(
      providerReason({ status: "unknown", errorCode: null, httpStatus: null })
    ).toBeNull();
  });

  it("surfaces operator-disabled with a stable phrase", () => {
    expect(
      providerReason({
        status: "not_requested",
        errorCode: "disabled_by_operator",
        httpStatus: null,
      })
    ).toBe("Disabled by operator");
  });

  it("does not invent a reason for skip-codes (the badge already covers them)", () => {
    expect(
      providerReason({
        status: "not_requested",
        errorCode: "no_components",
        httpStatus: null,
      })
    ).toBeNull();
    expect(
      providerReason({
        status: "not_requested",
        errorCode: "no_workflow_files",
        httpStatus: null,
      })
    ).toBeNull();
  });

  // LV-014: ``not_applicable`` keeps its explicit user-facing
  // semantic instead of being suppressed into the skip-code
  // null path; it must not degrade into failure / unavailable /
  // not_requested / clean-success phrasing either.
  it("renders the explicit Not applicable phrase for not_applicable", () => {
    expect(
      providerReason({
        status: "not_requested",
        errorCode: "not_applicable",
        httpStatus: null,
      })
    ).toBe("Not applicable");
  });

  it("renders a rate-limited phrase when only the status is available", () => {
    expect(
      providerReason({
        status: "rate_limited",
        errorCode: null,
        httpStatus: null,
      })
    ).toBe("Rate limited");
  });

  it("prefers the rate-limited phrase with the HTTP status when available", () => {
    expect(
      providerReason({
        status: "rate_limited",
        errorCode: "rate_limited",
        httpStatus: 429,
      })
    ).toBe("Rate limited · HTTP 429");
  });

  it("renders a generic request failure with the HTTP status when no taxonomy matches", () => {
    expect(
      providerReason({
        status: "unavailable",
        errorCode: null,
        httpStatus: 503,
      })
    ).toBe("Request failed · HTTP 503");
  });

  it("recognises 401 / 403 as authentication failures", () => {
    expect(
      providerReason({
        status: "unavailable",
        errorCode: null,
        httpStatus: 401,
      })
    ).toBe("Authentication failed · HTTP 401");
    expect(
      providerReason({
        status: "unavailable",
        errorCode: null,
        httpStatus: 403,
      })
    ).toBe("Authentication failed · HTTP 403");
  });

  it("treats the http_<digits> error code as an HTTP status", () => {
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "http_503",
        httpStatus: null,
      })
    ).toBe("Request failed · HTTP 503");
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "http_401",
        httpStatus: null,
      })
    ).toBe("Authentication failed · HTTP 401");
    expect(
      providerReason({
        status: "rate_limited",
        errorCode: "http_429",
        httpStatus: null,
      })
    ).toBe("Rate limited · HTTP 429");
  });

  it("renders Provider unavailable for provider-offline codes", () => {
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "provider_unavailable",
        httpStatus: null,
      })
    ).toBe("Provider unavailable");
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "upstream_5xx",
        httpStatus: null,
      })
    ).toBe("Provider unavailable");
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "provider_not_found",
        httpStatus: null,
      })
    ).toBe("Provider unavailable");
  });

  it("falls back to the neutral phrase for partial / unrecognised failures", () => {
    expect(
      providerReason({
        status: "partial",
        errorCode: "provider_partial",
        httpStatus: null,
      })
    ).toBe("Provider request failed");
    expect(
      providerReason({
        status: "unavailable",
        errorCode: "provider_internal_error",
        httpStatus: null,
      })
    ).toBe("Provider unavailable");
  });

  it("never invents an HTTP status when none is available", () => {
    const phrase = providerReason({
      status: "unavailable",
      errorCode: "provider_unavailable",
      httpStatus: null,
    });
    expect(phrase).toBe("Provider unavailable");
    // No 'HTTP ' substring must appear when there is no
    // structured HTTP status to back it up.
    expect(phrase).not.toMatch(/HTTP \d/);
  });
});

describe("providerHealthEntryReason", () => {
  it("does not show a raw URL-bearing failure summary verbatim", () => {
    // The rollup card used to render
    //   'HTTP GET https://api.securityscorecards.dev/...'
    // directly. With structured status + error_code, the
    // helper now returns a concise phrase and the card
    // therefore never displays the raw endpoint.
    const result = providerHealthEntryReason(
      entry({
        status: "unavailable",
        last_error_code: "http_503",
        redacted_failure_summary:
          "HTTP GET https://api.securityscorecards.dev/projects/github.com/o/r failed: 503 Service Unavailable",
      })
    );
    expect(result).toBe("Request failed · HTTP 503");
    expect(result).not.toMatch(/api\.securityscorecards\.dev/);
    expect(result).not.toMatch(/^HTTP GET /);
  });

  it("renders a generic request failure when no HTTP status is known", () => {
    const result = providerHealthEntryReason(
      entry({
        status: "unavailable",
        last_error_code: "provider_unavailable",
        redacted_failure_summary:
          "HTTPConnectionPool(...): Max retries exceeded with url: https://api.osv.dev/...",
      })
    );
    expect(result).toBe("Provider unavailable");
    expect(result).not.toMatch(/osv\.dev/);
  });

  it("preserves the raw redacted_failure_summary on the underlying entry", () => {
    // Contract: the helper is a pure presentation layer. The
    // raw technical evidence remains on the entry, untouched,
    // and remains available in tooltips / Diagnostics / logs.
    const e = entry({
      status: "unavailable",
      last_error_code: "http_503",
      redacted_failure_summary:
        "HTTP GET https://api.securityscorecards.dev/... failed: 503",
    });
    providerHealthEntryReason(e);
    expect(e.redacted_failure_summary).toBe(
      "HTTP GET https://api.securityscorecards.dev/... failed: 503"
    );
    expect(e.last_error_code).toBe("http_503");
  });

  it("renders null for available / cached / not_requested entries", () => {
    expect(
      providerHealthEntryReason(entry({ status: "available" }))
    ).toBeNull();
    expect(
      providerHealthEntryReason(entry({ status: "cached" }))
    ).toBeNull();
    expect(
      providerHealthEntryReason(entry({ status: "not_requested" }))
    ).toBeNull();
  });
});

describe("providerObservationReason", () => {
  it("uses the structured http_status when present", () => {
    const result = providerObservationReason(
      observation({
        status: "unavailable",
        error_code: null,
        http_status: 503,
        error_summary:
          "HTTP GET https://api.securityscorecards.dev/... failed: 503",
      })
    );
    expect(result).toBe("Request failed · HTTP 503");
  });

  it("does not display the raw error_summary verbatim", () => {
    const result = providerObservationReason(
      observation({
        status: "unavailable",
        error_code: null,
        http_status: 401,
        error_summary: "HTTP GET https://api.example.com/... 401 Unauthorized",
      })
    );
    expect(result).toBe("Authentication failed · HTTP 401");
    expect(result).not.toMatch(/api\.example\.com/);
  });

  it("falls back to the error_code taxonomy when http_status is null", () => {
    const result = providerObservationReason(
      observation({
        status: "unavailable",
        error_code: "http_503",
        http_status: null,
        error_summary: "upstream timed out",
      })
    );
    expect(result).toBe("Request failed · HTTP 503");
  });

  it("preserves the raw error_summary and http_status on the underlying observation", () => {
    const obs = observation({
      status: "unavailable",
      error_code: "http_503",
      http_status: 503,
      error_summary:
        "HTTP GET https://api.securityscorecards.dev/... failed: 503",
    });
    providerObservationReason(obs);
    expect(obs.error_summary).toBe(
      "HTTP GET https://api.securityscorecards.dev/... failed: 503"
    );
    expect(obs.http_status).toBe(503);
    expect(obs.error_code).toBe("http_503");
  });

  it("still surfaces operator-disabled and skip-codes correctly", () => {
    expect(
      providerObservationReason(
        observation({
          status: "not_requested",
          error_code: "disabled_by_operator",
          http_status: null,
        })
      )
    ).toBe("Disabled by operator");
    expect(
      providerObservationReason(
        observation({
          status: "not_requested",
          error_code: "no_components",
          http_status: null,
        })
      )
    ).toBeNull();
  });
});
