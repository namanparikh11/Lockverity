/**
 * Control-token transport tests (LV-007).
 *
 * The backend refuses any state-changing request that does not present
 * the per-runtime control token. That is what stops an arbitrary web
 * page from driving the local instance just because it is listening on
 * loopback. These tests pin the client half of that contract:
 *
 * 1. the token is read from the `<meta>` tag the backend injects into
 *    the document it serves;
 * 2. it is sent on every state-changing call, including uploads;
 * 3. it is never sent on reads, and never placed in a URL.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { apiClient, readControlToken } from "@/api/client";

const CONTROL_HEADER = "x-lockverity-control";
const META_NAME = "lockverity-control";
const TOKEN = "test-control-token-value";

interface FetchCall {
  url: string;
  init?: RequestInit;
}

function mockFetch() {
  const calls: FetchCall[] = [];
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: typeof input === "string" ? input : input.toString(), init });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  global.fetch = spy as unknown as typeof fetch;
  return calls;
}

/** Read a header out of a `RequestInit`, however it was supplied. */
function headerOf(init: RequestInit | undefined, name: string): string | null {
  const headers = init?.headers;
  if (!headers) return null;
  if (headers instanceof Headers) return headers.get(name);
  const bag = headers as Record<string, string>;
  const key = Object.keys(bag).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? bag[key] : null;
}

function installMeta(value: string): HTMLMetaElement {
  const meta = document.createElement("meta");
  meta.setAttribute("name", META_NAME);
  meta.setAttribute("content", value);
  document.head.appendChild(meta);
  return meta;
}

describe("control token delivery", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    document.head.querySelectorAll(`meta[name="${META_NAME}"]`).forEach((n) => n.remove());
  });

  afterEach(() => {
    global.fetch = originalFetch;
    document.head.querySelectorAll(`meta[name="${META_NAME}"]`).forEach((n) => n.remove());
    vi.restoreAllMocks();
  });

  it("reads the token from the meta tag the backend injected", () => {
    installMeta(TOKEN);
    expect(readControlToken()).toBe(TOKEN);
  });

  it("returns null when no token was delivered", () => {
    expect(readControlToken()).toBeNull();
  });

  it("ignores a blank meta value", () => {
    installMeta("   ");
    expect(readControlToken()).toBeNull();
  });

  it("picks up a token issued after a reload", () => {
    // The document is served `no-store`, so a reload issues a fresh
    // token. The client must not have cached the first one.
    const first = installMeta("first-token");
    expect(readControlToken()).toBe("first-token");
    first.remove();
    installMeta("second-token");
    expect(readControlToken()).toBe("second-token");
  });

  it("sends the token on POST", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.post("/scans/1/run");
    expect(calls).toHaveLength(1);
    expect(headerOf(calls[0].init, CONTROL_HEADER)).toBe(TOKEN);
  });

  it("sends the token on upload", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.upload("/repositories/upload", new Blob(["zip"]));
    expect(calls).toHaveLength(1);
    expect(headerOf(calls[0].init, CONTROL_HEADER)).toBe(TOKEN);
    // The browser must still own the multipart boundary.
    expect(headerOf(calls[0].init, "content-type")).toBeNull();
  });

  it("does not send the token on reads", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.get("/scans");
    await apiClient.getText("/scans/1/exports/findings_csv");
    expect(calls).toHaveLength(2);
    expect(headerOf(calls[0].init, CONTROL_HEADER)).toBeNull();
    expect(headerOf(calls[1].init, CONTROL_HEADER)).toBeNull();
  });

  it("never places the token in the URL", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.post("/scans/1/run");
    await apiClient.upload("/repositories/upload", new Blob(["zip"]));
    for (const call of calls) {
      expect(call.url).not.toContain(TOKEN);
      expect(call.url).not.toContain("control");
    }
  });

  it("still issues the request when no token is available", async () => {
    // The backend owns the policy; the client does not pre-emptively
    // fail, so the operator sees the real `forbidden` envelope.
    const calls = mockFetch();
    await apiClient.post("/scans/1/run");
    expect(calls).toHaveLength(1);
    expect(headerOf(calls[0].init, CONTROL_HEADER)).toBeNull();
  });

  it("does not persist the token anywhere", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.post("/scans/1/run");
    expect(calls).toHaveLength(1);
    expect(window.localStorage.getItem("lockverity-control")).toBeNull();
    expect(document.cookie).not.toContain(TOKEN);
  });

  it("keeps credentials omitted on state-changing calls", async () => {
    installMeta(TOKEN);
    const calls = mockFetch();
    await apiClient.post("/scans/1/run");
    expect(calls[0].init?.credentials).toBe("omit");
  });
});
