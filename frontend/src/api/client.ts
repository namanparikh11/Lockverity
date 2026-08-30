/**
 * Lockverity API client.
 *
 * - Configurable base URL via Vite env (`VITE_API_BASE_URL`).
 * - Typed responses mirroring the backend Pydantic schemas.
 * - Structured error parsing: every error carries a stable `code`
 *   so the UI can show a useful message without parsing the human
 *   description.
 * - Request cancellation via `AbortController`.
 * - Timeout handling: requests abort after a configurable
 *   `VITE_API_TIMEOUT_MS` (default 30 000 ms).
 * - No credentials and no stored tokens. State-changing calls carry the
 *   per-runtime control token the backend injected into this document;
 *   it lives in memory only, is never persisted, and is never placed in
 *   a URL. See `readControlToken`.
 * - No production fixture fallback.
 * - Development-only fixtures may exist behind
 *   `VITE_DEV_FIXTURES=enabled` and must never activate due to
 *   API failure.
 */

const DEFAULT_BASE_URL = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;

/**
 * Header carrying the per-runtime control token on state-changing
 * requests. The backend refuses any mutation that does not present it,
 * which is what stops an arbitrary web page from driving the local
 * instance just because it is listening on loopback.
 *
 * A custom header is deliberate: a browser cannot send one cross-origin
 * without a CORS preflight, and the backend grants none to a foreign
 * origin. The token is never placed in a URL, a cookie, or storage.
 */
const CONTROL_HEADER = "X-Lockverity-Control";
const CONTROL_META_NAME = "lockverity-control";

/** Methods the backend treats as state-changing. */
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * The only development-flag the API client recognises. A typo
 * (`VITE_DEV_FIXTURE=enabled`) MUST NOT activate fixtures; the
 * value must be exactly the string "enabled" (case-sensitive).
 */
const DEV_FIXTURE_FLAG = "enabled";

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  requestId?: string;
  httpStatus: number;
}

export class ApiClientError extends Error {
  public readonly apiError: ApiError;
  public readonly isAbort: boolean;
  public readonly isTimeout: boolean;

  constructor(apiError: ApiError, options: { isAbort?: boolean; isTimeout?: boolean } = {}) {
    super(apiError.message);
    this.name = "ApiClientError";
    this.apiError = apiError;
    this.isAbort = options.isAbort ?? false;
    this.isTimeout = options.isTimeout ?? false;
  }
}

export class ApiAbortError extends ApiClientError {
  constructor(message = "Request was cancelled.") {
    super(
      {
        code: "cancelled",
        message,
        httpStatus: 0,
      },
      { isAbort: true }
    );
    this.name = "ApiAbortError";
  }
}

export class ApiTimeoutError extends ApiClientError {
  constructor(timeoutMs: number) {
    super(
      {
        code: "timeout",
        message: `Request did not complete within ${timeoutMs} ms.`,
        httpStatus: 0,
      },
      { isTimeout: true }
    );
    this.name = "ApiTimeoutError";
  }
}

export interface RequestOptions {
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined | null>;
  headers?: Record<string, string>;
  /** Override the default timeout for this call. */
  timeoutMs?: number;
}

function readEnvNumber(value: unknown, fallback: number, max: number): number {
  if (typeof value !== "string") return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(parsed, max);
}

function readBaseUrl(): string {
  // Vite injects only vars that begin with VITE_.
  const env = (import.meta as unknown as { env?: Record<string, unknown> }).env;
  const fromEnv = env?.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.trim()) {
    return fromEnv.replace(/\/+$/, "");
  }
  return DEFAULT_BASE_URL;
}

function readTimeoutMs(): number {
  const env = (import.meta as unknown as { env?: Record<string, unknown> }).env;
  return readEnvNumber(env?.VITE_API_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS);
}

/**
 * Returns true only if the development fixture flag is set to the
 * exact string "enabled". Any other value, including the empty
 * string, an unrelated variable, or any case variation, returns
 * false. Fixtures are an explicit developer action; they are never
 * activated by API failure.
 */
export function readDevFixturesEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, unknown> }).env;
  return env?.VITE_DEV_FIXTURES === DEV_FIXTURE_FLAG;
}

/**
 * Read the control token issued to this window.
 *
 * In the shipped single-port runtime the backend injects the token into
 * the `index.html` it serves, so the meta tag is always present and the
 * value is unique to the running process. In the two-port Vite dev
 * workflow the dev server serves the document instead, so the developer
 * pins both sides with `LOCKVERITY_CONTROL_TOKEN` / `VITE_CONTROL_TOKEN`.
 *
 * Read on every call rather than cached at module load: the document is
 * served `no-store` and a reload issues a fresh token.
 */
export function readControlToken(): string | null {
  if (typeof document !== "undefined") {
    const meta = document.querySelector(`meta[name="${CONTROL_META_NAME}"]`);
    const fromMeta = meta?.getAttribute("content")?.trim();
    if (fromMeta) return fromMeta;
  }
  const env = (import.meta as unknown as { env?: Record<string, unknown> }).env;
  const fromEnv = env?.VITE_CONTROL_TOKEN;
  if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv.trim();
  return null;
}

/**
 * Attach the control token to a header bag for a state-changing call.
 * A missing token is not an error here: the request proceeds and the
 * backend returns the `forbidden` envelope, so the failure is reported
 * by the one component that actually knows the policy.
 */
function withControlToken(headers: Record<string, string>): Record<string, string> {
  const token = readControlToken();
  if (token) headers[CONTROL_HEADER] = token;
  return headers;
}

function buildUrl(
  base: string,
  path: string,
  query?: RequestOptions["query"]
): string {
  const url = new URL(
    path.startsWith("/") ? `${base}${path}` : `${base}/${path}`,
    window.location.origin
  );
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) {
        continue;
      }
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function parseError(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = "Request failed.";
  let details: Record<string, unknown> | undefined;
  let requestId: string | undefined;
  try {
    const body = (await response.json()) as {
      error?: {
        code?: string;
        message?: string;
        details?: Record<string, unknown>;
        request_id?: string;
      };
    };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
      requestId = body.error.request_id;
    }
  } catch {
    // Non-JSON error body. The defaults above are safe.
  }
  if (response.headers.get("x-request-id")) {
    requestId = response.headers.get("x-request-id") ?? requestId;
  }
  return {
    code,
    message,
    details,
    requestId,
    httpStatus: response.status,
  };
}

function combineSignals(
  external: AbortSignal | undefined,
  internal: AbortController
): AbortSignal {
  if (!external) return internal.signal;
  if (external.aborted) {
    internal.abort();
    return internal.signal;
  }
  const onExternalAbort = () => {
    internal.abort();
  };
  external.addEventListener("abort", onExternalAbort, { once: true });
  return internal.signal;
}

async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
  body?: unknown
): Promise<T> {
  const base = readBaseUrl();
  const url = buildUrl(base, path, options.query);
  const timeoutController = new AbortController();
  const timeoutMs = options.timeoutMs ?? readTimeoutMs();
  const timer = window.setTimeout(() => timeoutController.abort(), timeoutMs);
  const signal = combineSignals(options.signal, timeoutController);
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers ?? {}),
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (MUTATING_METHODS.has(method.toUpperCase())) {
    withControlToken(headers);
  }
  const init: RequestInit = {
    method,
    headers,
    signal,
    credentials: "omit",
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    window.clearTimeout(timer);
    if ((err as { name?: string })?.name === "AbortError") {
      if (options.signal?.aborted) {
        throw new ApiAbortError();
      }
      throw new ApiTimeoutError(timeoutMs);
    }
    throw new ApiClientError({
      code: "network_error",
      message: err instanceof Error ? err.message : "Network request failed.",
      httpStatus: 0,
    });
  }
  window.clearTimeout(timer);

  if (!response.ok) {
    throw new ApiClientError(await parseError(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/**
 * Read a response as `text/plain` for downloads (exports).
 * Errors are mapped to the same envelope as JSON calls.
 */
async function requestText(
  path: string,
  options: RequestOptions = {}
): Promise<{ body: string; contentType: string; filename: string | null }> {
  const base = readBaseUrl();
  const url = buildUrl(base, path, options.query);
  const timeoutController = new AbortController();
  const timeoutMs = options.timeoutMs ?? readTimeoutMs();
  const timer = window.setTimeout(() => timeoutController.abort(), timeoutMs);
  const signal = combineSignals(options.signal, timeoutController);
  const init: RequestInit = {
    method: "GET",
    headers: { Accept: "application/json, text/plain;q=0.9, */*;q=0.5" },
    signal,
    credentials: "omit",
  };
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    window.clearTimeout(timer);
    if ((err as { name?: string })?.name === "AbortError") {
      if (options.signal?.aborted) throw new ApiAbortError();
      throw new ApiTimeoutError(timeoutMs);
    }
    throw new ApiClientError({
      code: "network_error",
      message: err instanceof Error ? err.message : "Network request failed.",
      httpStatus: 0,
    });
  }
  window.clearTimeout(timer);
  if (!response.ok) {
    throw new ApiClientError(await parseError(response));
  }
  const contentDisposition = response.headers.get("content-disposition");
  const filenameMatch = contentDisposition?.match(/filename="?([^"]+)"?/);
  return {
    body: await response.text(),
    contentType: response.headers.get("content-type") ?? "application/octet-stream",
    filename: filenameMatch?.[1] ?? null,
  };
}

/**
 * Upload a single file as multipart/form-data. Used for archive
 * uploads. The file is passed as a Blob; the client never reads
 * its bytes into JavaScript memory beyond what the browser does
 * during fetch.
 */
async function requestUpload<T>(
  path: string,
  file: File | Blob,
  options: RequestOptions = {}
): Promise<T> {
  const base = readBaseUrl();
  const url = buildUrl(base, path, options.query);
  const timeoutController = new AbortController();
  const timeoutMs = options.timeoutMs ?? MAX_TIMEOUT_MS;
  const timer = window.setTimeout(() => timeoutController.abort(), timeoutMs);
  const signal = combineSignals(options.signal, timeoutController);
  const form = new FormData();
  // The backend's intake route accepts the upload under
  // the ``file`` form field (see
  // ``backend/app/api/intake.py:create_uploaded_repository``).
  // An earlier client used ``archive`` which the FastAPI
  // handler would silently bind to ``None`` and surface as
  // a 422 validation error. The v1.5 guided intake flow
  // pins the field name to match the backend contract.
  form.append("file", file);
  const init: RequestInit = {
    method: "POST",
    // The upload is a state-changing request like any other; the
    // control token is required. ``Content-Type`` is deliberately left
    // to the browser so the multipart boundary is generated correctly.
    headers: withControlToken({ Accept: "application/json" }),
    signal,
    credentials: "omit",
    body: form,
  };
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    window.clearTimeout(timer);
    if ((err as { name?: string })?.name === "AbortError") {
      if (options.signal?.aborted) throw new ApiAbortError();
      throw new ApiTimeoutError(timeoutMs);
    }
    throw new ApiClientError({
      code: "network_error",
      message: err instanceof Error ? err.message : "Network request failed.",
      httpStatus: 0,
    });
  }
  window.clearTimeout(timer);
  if (!response.ok) {
    throw new ApiClientError(await parseError(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>("GET", path, options);
  },
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>("POST", path, options, body);
  },
  getText(path: string, options?: RequestOptions) {
    return requestText(path, options);
  },
  upload<T>(path: string, file: File | Blob, options?: RequestOptions) {
    return requestUpload<T>(path, file, options);
  },
};

export function describeError(err: unknown): string {
  if (err instanceof ApiClientError) {
    return err.apiError.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "Unknown error.";
}

/**
 * Map an unknown error to one of a small set of user-facing
 * categories. Used by error banners so the UI can pick an icon and
 * copy without parsing error text.
 */
export type ErrorCategory =
  | "cancelled"
  | "timeout"
  | "network"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "invalid_ref"
  | "validation"
  | "rate_limited"
  | "provider_unavailable"
  | "duplicate"
  | "rescan_source_unavailable"
  | "internal_unexpected"
  | "server"
  | "unknown";

export function categorizeError(err: unknown): ErrorCategory {
  if (err instanceof ApiAbortError) return "cancelled";
  if (err instanceof ApiTimeoutError) return "timeout";
  if (err instanceof ApiClientError) {
    const code = err.apiError.code;
    const status = err.apiError.httpStatus;
    if (code === "cancelled" || err.isAbort) return "cancelled";
    if (code === "timeout" || err.isTimeout) return "timeout";
    if (code === "network_error" || status === 0) return "network";
    if (code === "unauthorized" || status === 401) return "unauthorized";
    if (code === "forbidden" || status === 403) return "forbidden";
    if (code === "not_found" || status === 404) return "not_found";
    // v2.1.1: ``invalid_ref`` is a 422 envelope
    // distinct from the generic ``validation_error``
    // (also 422). The check is code-first because the
    // stable error code is the source of truth; the
    // status code (422) is a fallback. The two
    // categories render different actionable messages
    // in the UI.
    if (code === "invalid_ref" || (status === 422 && code === "invalid_ref")) {
      return "invalid_ref";
    }
    if (code === "rate_limited" || status === 429) return "rate_limited";
    if (code === "duplicate" || status === 409) return "duplicate";
    // v2.1.1: archive-rejection envelopes (400) and
    // path-unsafe envelopes (400) render as a
    // validation banner so the operator sees the
    // category-specific archive message instead of
    // the generic "Unknown error" default. The
    // stable code is checked first; the 400 status
    // is a fallback so future 400 envelopes are
    // also classified.
    if (
      code === "archive_unsafe" ||
      code === "path_unsafe" ||
      (status === 400 && code !== "")
    ) {
      return "validation";
    }
    if (code === "provider_unavailable" || status === 502 || status === 503) {
      return "provider_unavailable";
    }
    // v1.6.1: rescan_source_unavailable must be
    // checked before ``status === 422`` (which the
    // route uses) so the workbench can render the
    // bounded "source is no longer available"
    // guidance. The check is code-first because the
    // stable error code is the source of truth; the
    // status code is a fallback.
    if (code === "rescan_source_unavailable") {
      return "rescan_source_unavailable";
    }
    // v2.1.1: ``internal_unexpected`` carries a
    // non-PII ``correlation_id`` in the response
    // ``details`` envelope; the UI surfaces the id
    // alongside the message so the operator can
    // cross-reference the local log.
    if (code === "internal_unexpected") {
      return "internal_unexpected";
    }
    if (code === "validation_error" || status === 422) return "validation";
    if (status >= 500) return "server";
    return "unknown";
  }
  return "unknown";
}

/**
 * Return the safe correlation id from an ``internal_unexpected``
 * ``ApiClientError``, when present. The id is a 16-character
 * lowercase hex string (``secrets.token_hex(8)``); the format
 * is documented as ``^[0-9a-f]{16}$``. Returns ``null`` for
 * errors that are not ``internal_unexpected`` or that do not
 * carry the id in their details.
 */
export function correlationIdFromError(err: unknown): string | null {
  if (!(err instanceof ApiClientError)) return null;
  if (err.apiError.code !== "internal_unexpected") return null;
  const details = err.apiError.details ?? {};
  const cid = (details as { correlation_id?: unknown }).correlation_id;
  if (typeof cid !== "string") return null;
  if (!/^[0-9a-f]{16}$/.test(cid)) return null;
  return cid;
}
