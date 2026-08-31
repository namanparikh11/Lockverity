/**
 * v1.5 guided intake / analyze flow tests.
 *
 * The /analyze page is a thin wrapper around the existing
 * intake endpoints. It must:
 *
 * - render two clearly separated intake methods,
 * - submit the GitHub URL through ``POST /repositories/github``,
 * - submit the ZIP archive through ``POST /repositories/upload``
 *   using the multipart ``file`` form field (matching the
 *   backend contract),
 * - prevent duplicate submissions,
 * - navigate to ``/scans/{id}`` on success,
 * - render the bounded error envelope honestly,
 * - surface the AppShell primary nav ``Analyze`` entry,
 * - keep the /demo route working.
 *
 * The page reuses the existing ``usePolling`` hook for the
 * status panel; we do not stub the polling behaviour here.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

import { AppShell } from "@/layouts/AppShell";
import { AnalyzePage } from "@/pages/AnalyzePage";
import { DemoHomePage } from "@/pages/DemoHomePage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeIntakeResult(overrides: {
  scanId?: number;
  repositoryId?: number;
} = {}) {
  const scanId = overrides.scanId ?? 12;
  const repositoryId = overrides.repositoryId ?? 7;
  return {
    repository: {
      id: repositoryId,
      source_type: "github",
      provider: "github",
      owner: "octocat",
      name: "hello-world",
      canonical_url: "https://github.com/octocat/hello-world",
      default_branch: "main",
      description: null,
      visibility: "public",
      archived: false,
      last_provider_sync_at: null,
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
    },
    scan: {
      id: scanId,
      repository_id: repositoryId,
      status: "queued",
      trigger_type: "manual",
      requested_ref: null,
      resolved_commit_sha: null,
      analyzer_version: "lockverity 1.5.0",
      started_at: null,
      completed_at: null,
      failure_code: null,
      failure_summary: null,
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
    },
    workspace: {
      id: 1,
      scan_run_id: scanId,
      workspace_key: "wks-" + "a".repeat(32),
      kind: "github",
      state: "quarantined",
      archive_filename: "github/octocat/hello-world.tar.gz",
      archive_sha256: null,
      archive_size: 0,
      file_count: 0,
      uncompressed_size: 0,
      failure_code: null,
      failure_summary: null,
      ready_at: null,
      cleaned_up_at: null,
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
    },
    intake_summary: {
      kind: "github",
      owner: "octocat",
      name: "hello-world",
    },
  };
}

describe("v1.5 guided intake / analyze flow", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders both intake methods and the bounded non-execution / hostile-archive copy", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({
        name: "Lockverity",
        version: "1.6.1",
        tagline: "Evidence-first software supply-chain assurance",
        environment: "test",
        api_prefix: "/api/v1",
        archive_limits: {},
        pagination: {},
        provider_safety: {},
        intake: {},
      })
    );
    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    // Two intake method headings.
    expect(
      screen.getByRole("heading", { name: /public github repository/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /source archive upload/i })
    ).toBeInTheDocument();

    // The bounded non-execution and archive-hostility copy
    // must be present. The page renders it across two
    // JSX lines, so we match substrings.
    expect(
      screen.getAllByText(/never executes repository code/i).length
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/archives are treated as hostile input/i)
    ).toBeInTheDocument();

    // The two primary action buttons exist.
    expect(
      screen.getByRole("button", { name: /^analyze repository$/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^analyze archive$/i })
    ).toBeInTheDocument();

    // The AppShell footer reports the v1.5.0 version.
    await waitFor(() => {
      expect(screen.getByText("v1.6.1")).toBeInTheDocument();
    });
  });

  it("submits the GitHub URL through POST /repositories/github and prevents duplicate submits", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/v1/system/info")) {
        return Promise.resolve(
          jsonResponse({
            name: "Lockverity",
            version: "1.6.1",
            tagline: "Evidence-first software supply-chain assurance",
            environment: "test",
            api_prefix: "/api/v1",
            archive_limits: {},
            pagination: {},
            provider_safety: {},
            intake: {},
          })
        );
      }
      if (
        url.endsWith("/api/v1/repositories/github") &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(makeIntakeResult({ scanId: 42 }), 201));
      }
      // v1.6: the page now also calls ``/scans/{id}/run``
      // to schedule execution on the local worker. The
      // mock returns a 200 with the scan shape so the
      // scan is recorded as ``started``.
      if (url.match(/\/api\/v1\/scans\/\d+\/run$/) && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          external_evidence_providers: {
            osv: false,
            deps_dev: true,
            openssf: true,
          },
        });
        return Promise.resolve(
          jsonResponse({
            id: 42,
            repository_id: 7,
            status: "running",
            trigger_type: "manual",
            requested_ref: null,
            resolved_commit_sha: null,
            analyzer_version: "lockverity 1.6.0",
            started_at: "2026-07-18T00:00:00Z",
            completed_at: null,
            failure_code: null,
            failure_summary: null,
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:00Z",
          })
        );
      }
      // The status poll: the page keeps polling the scan
      // after intake. We respond with a terminal "completed"
      // status so the polling hook stops quickly.
      if (url.match(/\/api\/v1\/scans\/\d+$/)) {
        return Promise.resolve(
          jsonResponse({
            id: 42,
            repository_id: 7,
            status: "completed",
            trigger_type: "manual",
            requested_ref: null,
            resolved_commit_sha: "0123456789abcdef0123456789abcdef01234567",
            analyzer_version: "lockverity 1.5.0",
            started_at: "2026-07-18T00:00:00Z",
            completed_at: "2026-07-18T00:00:01Z",
            failure_code: null,
            failure_summary: null,
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:01Z",
          })
        );
      }
      return Promise.resolve(jsonResponse({}, 404));
    });
    global.fetch = fetchMock;

    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const urlInput = screen.getByLabelText(/public github url/i);
    const submitButton = screen.getByRole("button", { name: /^analyze repository$/i });
    const githubForm = screen.getByLabelText("Analyze public GitHub repository form");

    await act(async () => {
      fireEvent.change(urlInput, {
        target: { value: "https://github.com/octocat/hello-world" },
      });
      fireEvent.click(within(githubForm).getByRole("checkbox", { name: /OSV/i }));
    });
    await act(async () => {
      fireEvent.click(submitButton);
    });

    // The page should have POSTed exactly once to the
    // /repositories/github endpoint and the second click
    // must be a no-op (the button is disabled while
    // submitting, and stays disabled after the scan id
    // is recorded so duplicate submission is impossible).
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter((args) => {
        const u = args[0] as unknown;
        const init = args[1] as RequestInit | undefined;
        const url = typeof u === "string" ? u : (u as URL).toString();
        return (
          url.endsWith("/api/v1/repositories/github") &&
          init?.method === "POST"
        );
      });
      expect(calls.length).toBe(1);
    });

    // Duplicate click does not re-issue the POST.
    await act(async () => {
      fireEvent.click(submitButton);
    });
    const calls = fetchMock.mock.calls.filter((args) => {
      const u = args[0] as unknown;
      const url = typeof u === "string" ? u : (u as URL).toString();
      return url.endsWith("/api/v1/repositories/github");
    });
    expect(calls.length).toBe(1);

    // The status panel is rendered with the new scan id.
    expect(
      screen.getByText(/scan #42 started/i)
    ).toBeInTheDocument();
  });

  it("submits the ZIP upload through POST /repositories/upload with the multipart 'file' field", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/api/v1/system/info")) {
          return jsonResponse({
            name: "Lockverity",
            version: "1.6.1",
            tagline: "Evidence-first software supply-chain assurance",
            environment: "test",
            api_prefix: "/api/v1",
            archive_limits: {},
            pagination: {},
            provider_safety: {},
            intake: {},
          });
        }
        if (
          url.endsWith("/api/v1/repositories/upload") &&
          init?.method === "POST"
        ) {
          // The v1.5 contract: the multipart field MUST be
          // ``file`` (matching the backend's
          // ``UploadFile = File(...)`` declaration). The
          // legacy field name ``archive`` is the dead-end
          // the v1.5 fix removes.
          const form = init.body as FormData;
          expect(form.has("file")).toBe(true);
          expect(form.has("archive")).toBe(false);
          return jsonResponse(makeIntakeResult({ scanId: 99, repositoryId: 13 }), 201);
        }
        // v1.6: the page also calls ``/scans/{id}/run`` to
        // schedule execution on the local worker.
        if (url.match(/\/api\/v1\/scans\/\d+\/run$/) && init?.method === "POST") {
          expect(JSON.parse(String(init.body))).toEqual({
            external_evidence_providers: {
              osv: true,
              deps_dev: false,
              openssf: true,
            },
          });
          return jsonResponse({
            id: 99,
            repository_id: 13,
            status: "running",
            trigger_type: "upload",
            requested_ref: null,
            resolved_commit_sha: null,
            analyzer_version: "lockverity 1.6.0",
            started_at: "2026-07-18T00:00:00Z",
            completed_at: null,
            failure_code: null,
            failure_summary: null,
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:00Z",
          });
        }
        if (url.match(/\/api\/v1\/scans\/\d+$/)) {
          return jsonResponse({
            id: 99,
            repository_id: 13,
            status: "completed",
            trigger_type: "upload",
            requested_ref: null,
            resolved_commit_sha: null,
            analyzer_version: "lockverity 1.5.0",
            started_at: "2026-07-18T00:00:00Z",
            completed_at: "2026-07-18T00:00:01Z",
            failure_code: null,
            failure_summary: null,
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:01Z",
          });
        }
        return jsonResponse({}, 404);
      }
    );
    global.fetch = fetchMock as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const archiveForm = screen.getByLabelText("Analyze uploaded source archive form");
    expect(
      within(archiveForm).getByRole("checkbox", { name: /OpenSSF Scorecard/i })
    ).toBeDisabled();
    fireEvent.click(within(archiveForm).getByRole("checkbox", { name: /deps\.dev/i }));

    // The page hides the file input behind a "Choose file"
    // button. We poke the hidden <input type="file"> directly.
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    expect(fileInput).not.toBeNull();
    const zipBlob = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], {
      type: "application/zip",
    });
    const file = new File([zipBlob], "fixture.zip", { type: "application/zip" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });

    const submitButton = screen.getByRole("button", { name: /^analyze archive$/i });
    await act(async () => {
      fireEvent.click(submitButton);
    });

    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter((args) => {
        const u = args[0] as unknown;
        const url = typeof u === "string" ? u : (u as URL).toString();
        return url.endsWith("/api/v1/repositories/upload");
      });
      expect(calls.length).toBe(1);
    });

    expect(
      screen.getByText(/scan #99 started/i)
    ).toBeInTheDocument();
  });

  it("renders the API error envelope honestly on validation failure", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/api/v1/system/info")) {
          return jsonResponse({
            name: "Lockverity",
            version: "1.6.1",
            tagline: "Evidence-first software supply-chain assurance",
            environment: "test",
            api_prefix: "/api/v1",
            archive_limits: {},
            pagination: {},
            provider_safety: {},
            intake: {},
          });
        }
        if (
          url.endsWith("/api/v1/repositories/github") &&
          init?.method === "POST"
        ) {
          return new Response(
            JSON.stringify({
              error: {
                code: "validation_error",
                message: "Repository URL is not a valid public GitHub URL.",
              },
            }),
            { status: 422, headers: { "content-type": "application/json" } }
          );
        }
        return jsonResponse({}, 404);
      }
    );
    global.fetch = fetchMock as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const urlInput = screen.getByLabelText(/public github url/i);
    await act(async () => {
      fireEvent.change(urlInput, {
        target: { value: "https://github.com/octocat/hello-world" },
      });
    });
    const submitButton = screen.getByRole("button", { name: /^analyze repository$/i });
    await act(async () => {
      fireEvent.click(submitButton);
    });

    // The page renders the stable backend error envelope
    // message verbatim. No stack traces, no fabricated
    // "something went wrong" wording.
    await waitFor(() => {
      expect(
        screen.getByText(
          /Repository URL is not a valid public GitHub URL\./i
        )
      ).toBeInTheDocument();
    });
    // The button is re-enabled after the error so the
    // reviewer can correct and retry.
    expect(submitButton).not.toBeDisabled();
  });

  it("navigates to /scans/{id} when the Open scan button is clicked", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/api/v1/system/info")) {
          return jsonResponse({
            name: "Lockverity",
            version: "1.6.1",
            tagline: "Evidence-first software supply-chain assurance",
            environment: "test",
            api_prefix: "/api/v1",
            archive_limits: {},
            pagination: {},
            provider_safety: {},
            intake: {},
          });
        }
        if (
          url.endsWith("/api/v1/repositories/github") &&
          init?.method === "POST"
        ) {
          return jsonResponse(makeIntakeResult({ scanId: 314 }), 201);
        }
        if (url.match(/\/api\/v1\/scans\/\d+$/)) {
          return jsonResponse({
            id: 314,
            repository_id: 7,
            status: "completed",
            trigger_type: "manual",
            requested_ref: null,
            resolved_commit_sha: "0123456789abcdef0123456789abcdef01234567",
            analyzer_version: "lockverity 1.5.0",
            started_at: "2026-07-18T00:00:00Z",
            completed_at: "2026-07-18T00:00:01Z",
            failure_code: null,
            failure_summary: null,
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:01Z",
          });
        }
        return jsonResponse({}, 404);
      }
    );
    global.fetch = fetchMock as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
            <Route
              path="/scans/:scanId"
              element={<div>scan-detail-page</div>}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const urlInput = screen.getByLabelText(/public github url/i);
    await act(async () => {
      fireEvent.change(urlInput, {
        target: { value: "https://github.com/octocat/hello-world" },
      });
    });
    const submitButton = screen.getByRole("button", { name: /^analyze repository$/i });
    await act(async () => {
      fireEvent.click(submitButton);
    });

    // The Open scan button is the navigation hook.
    const openButton = await screen.findByRole("button", { name: /^open scan$/i });
    await act(async () => {
      fireEvent.click(openButton);
    });
    expect(screen.getByText("scan-detail-page")).toBeInTheDocument();
  });

  it("exposes an Analyze entry in the AppShell primary nav", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({
        name: "Lockverity",
        version: "1.6.0",
        tagline: "Evidence-first software supply-chain assurance",
        environment: "test",
        api_prefix: "/api/v1",
        archive_limits: {},
        pagination: {},
        provider_safety: {},
        intake: {},
      })
    );
    render(
      <MemoryRouter initialEntries={["/analyze"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/analyze" element={<AnalyzePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    const navLink = screen.getByRole("link", { name: /^Analyze$/ });
    expect(navLink).toBeInTheDocument();
    expect(navLink.getAttribute("href")).toBe("/analyze");
  });

  it("does not break the /demo route (v1.4 surface still works)", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({
        name: "Lockverity",
        version: "1.6.0",
        tagline: "Evidence-first software supply-chain assurance",
        environment: "test",
        api_prefix: "/api/v1",
        archive_limits: {},
        pagination: {},
        provider_safety: {},
        intake: {},
      })
    );
    render(
      <MemoryRouter initialEntries={["/demo"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/demo" element={<DemoHomePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(
      screen.getByRole("heading", { name: /local demo/i })
    ).toBeInTheDocument();
    // The v1.5 "beyond the demo" section is present.
    expect(
      screen.getByRole("heading", { name: /analyze your own repository/i })
    ).toBeInTheDocument();
    // The /analyze link from the v1.5 demo section is
    // reachable.
    const analyzeLinks = screen.getAllByRole("link", {
      name: /^\/analyze$/,
    });
    expect(analyzeLinks.length).toBeGreaterThan(0);
  });
});
