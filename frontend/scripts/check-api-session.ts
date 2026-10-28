// Mocked-fetch regression checks for frontend/src/api.ts. Native Node 24
// TypeScript stripping only: no React, DOM, real fetch, network, env or
// account credentials. This exercises the mocked global fetch boundary;
// it is NOT browser/mobile QA and must not be reported as such.
// Run from repo root:
//   node --experimental-strip-types frontend/scripts/check-api-session.ts
import assert from "node:assert/strict";
import {
  ApiError,
  apiGet,
  apiPost,
  clearAccessToken,
  isValidAccessKeyShape,
  registerUnauthorizedCallback,
  setAccessToken,
} from "../src/api.ts";

// Fixed synthetic tokens only (never env/account values).
const TOKEN_A = "SyntheticA_0123456789-abcdefghij";
const TOKEN_B = "SyntheticB_9876543210-fedcba9876";
const CANARY = "SYNTHETIC-CANARY-never-display-this";
const CANARY_UPSTREAM = "SYNTHETIC-UPSTREAM-secret-text";

const originalFetch = globalThis.fetch;
type Call = { url: unknown; init: any };
let calls: Call[] = [];
let activeUnregister: (() => void) | null = null;

// Default fetch is a throwing sentinel: any unexpected (real) call fails.
(globalThis as any).fetch = () => {
  throw new Error("unexpected fetch: mocked boundary not installed");
};

function installFetch(impl: (url: any, init: any) => any): Call[] {
  calls = [];
  const captured = calls;
  (globalThis as any).fetch = (url: any, init: any) => {
    captured.push({ url, init });
    return impl(url, init);
  };
  return captured;
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason?: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeResponse(status: number, body: string | (() => Promise<string>)): any {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: typeof body === "function" ? body : async () => body,
  };
}

function setCallback(callback: () => void): () => void {
  if (activeUnregister) activeUnregister();
  activeUnregister = registerUnauthorizedCallback(callback);
  return activeUnregister;
}

function resetSession(): void {
  if (activeUnregister) {
    activeUnregister();
    activeUnregister = null;
  }
  clearAccessToken();
}

function expectApiError(err: unknown, status: number, pattern?: RegExp): boolean {
  assert.ok(err instanceof ApiError, "expected ApiError");
  assert.equal((err as ApiError).status, status);
  const message = (err as ApiError).message;
  assert.ok(!message.includes(CANARY), "error leaked response canary");
  assert.ok(!message.includes(CANARY_UPSTREAM), "error leaked upstream canary");
  assert.ok(!message.includes(TOKEN_A), "error leaked token A");
  assert.ok(!message.includes(TOKEN_B), "error leaked token B");
  if (pattern) assert.match(message, pattern);
  return true;
}

function authOf(call: Call): string | null {
  return call.init.headers.get("Authorization");
}

async function main(): Promise<void> {
  // 1) Successful GET/POST through fake fetch.
  {
    resetSession();
    setAccessToken(TOKEN_A);
    const seen = installFetch(async () => fakeResponse(200, JSON.stringify({ ok: true, n: 7 })));
    const got: any = await apiGet("/api/cases");
    assert.deepEqual(got, { ok: true, n: 7 });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, "/api/cases");
    assert.equal(authOf(seen[0]), `Bearer ${TOKEN_A}`);
    assert.equal(seen[0].init.redirect, "error");
    assert.ok(!String(seen[0].url).includes(TOKEN_A), "token in URL");

    const posted: any = await apiPost("/api/investigations", { note: "synthetic", quantity: 2 });
    assert.deepEqual(posted, { ok: true, n: 7 });
    assert.equal(seen.length, 2);
    assert.equal(seen[1].url, "/api/investigations");
    assert.equal(seen[1].init.method, "POST");
    assert.equal(seen[1].init.redirect, "error");
    assert.equal(authOf(seen[1]), `Bearer ${TOKEN_A}`);
    assert.equal(seen[1].init.headers.get("Content-Type"), "application/json");
    assert.equal(seen[1].init.body, JSON.stringify({ note: "synthetic", quantity: 2 }));
    assert.ok(!String(seen[1].init.body).includes(TOKEN_A), "token in body");
    assert.ok(!String(seen[1].url).includes(TOKEN_A), "token in POST URL");
  }

  // 2a) Deferred old-session success resolving after token switch: stale.
  {
    resetSession();
    setAccessToken(TOKEN_A);
    const pending = deferred<any>();
    installFetch(() => pending.promise);
    const requestPromise: Promise<any> = apiGet("/api/cases");
    const expectation = assert.rejects(requestPromise, (e: unknown) => expectApiError(e, 0, /session changed/));
    setAccessToken(TOKEN_B);
    pending.resolve(fakeResponse(200, JSON.stringify({ stale: true })));
    await expectation; // stale result discarded, never delivered
  }

  // 2b) Session change after Response.text() has entered but while its
  //     body is still pending: stale for both success and 401 bodies.
  {
    resetSession();
    let unauthorized = 0;
    setCallback(() => { unauthorized += 1; });
    for (const status of [200, 401]) {
      setAccessToken(TOKEN_A);
      const bodyEntered = deferred<boolean>();
      const textPending = deferred<string>();
      let entries = 0;
      installFetch(async () => fakeResponse(status, () => {
        entries += 1;
        bodyEntered.resolve(true);
        return textPending.promise;
      }));
      const requestPromise: Promise<any> = apiGet("/api/cases");
      const expectation = assert.rejects(requestPromise, (e: unknown) => expectApiError(e, 0, /session changed/));
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          bodyEntered.promise,
          new Promise<never>((_resolve, reject) => {
            timeoutId = setTimeout(() => reject(new Error("response.text was not entered")), 1000);
          }),
        ]);
      } finally {
        if (timeoutId !== undefined) clearTimeout(timeoutId);
      }
      assert.equal(entries, 1, "response.text must enter exactly once before session change");
      setAccessToken(TOKEN_B);
      textPending.resolve(status === 200 ? JSON.stringify({ stale: true }) : CANARY + " " + TOKEN_A);
      await expectation;
      assert.equal(entries, 1, "response.text must not be entered again");
      assert.equal(unauthorized, 0, "pending-body stale response fired unauthorized callback");
      const seen = installFetch(async () => fakeResponse(200, JSON.stringify({ fresh: true })));
      const fresh: any = await apiGet("/api/cases");
      assert.deepEqual(fresh, { fresh: true });
      assert.equal(authOf(seen[0]), `Bearer ${TOKEN_B}`, "next request must use token B");
    }
    assert.equal(unauthorized, 0);
  }

  // 2c) Deferred old-session 401 after new sign-in: newer session survives,
  //     no unauthorized callback, next request carries the new token.
  {
    resetSession();
    let unauthorized = 0;
    setCallback(() => { unauthorized += 1; });
    setAccessToken(TOKEN_A);
    const pending = deferred<any>();
    installFetch(() => pending.promise);
    const requestPromise: Promise<any> = apiGet("/api/cases");
    const expectation = assert.rejects(requestPromise, (e: unknown) => expectApiError(e, 0, /session changed/));
    setAccessToken(TOKEN_B);
    pending.resolve(fakeResponse(401, CANARY + " " + TOKEN_A));
    await expectation;
    assert.equal(unauthorized, 0, "stale 401 fired unauthorized callback");
    const seen = installFetch(async () => fakeResponse(200, JSON.stringify({ fresh: true })));
    const fresh: any = await apiGet("/api/cases");
    assert.deepEqual(fresh, { fresh: true });
    assert.equal(authOf(seen[0]), `Bearer ${TOKEN_B}`);
    assert.equal(unauthorized, 0);
  }

  // 2d) Fetch rejection after token switch: stale, newer token not cleared.
  {
    resetSession();
    let unauthorized = 0;
    setCallback(() => { unauthorized += 1; });
    setAccessToken(TOKEN_A);
    const pending = deferred<any>();
    installFetch(() => pending.promise);
    const requestPromise: Promise<any> = apiGet("/api/cases");
    const expectation = assert.rejects(requestPromise, (e: unknown) => expectApiError(e, 0, /session changed/));
    setAccessToken(TOKEN_B);
    pending.reject(new Error(CANARY_UPSTREAM));
    await expectation;
    assert.equal(unauthorized, 0);
    const seen = installFetch(async () => fakeResponse(200, "null"));
    await apiGet("/api/cases");
    assert.equal(authOf(seen[0]), `Bearer ${TOKEN_B}`, "old failure cleared newer token");
  }

  // 3) Current-session 401: clears token, callback exactly once, body never
  //    shown; old unregister handle must not remove a newer callback.
  {
    resetSession();
    let first = 0;
    let second = 0;
    const unregisterFirst = setCallback(() => { first += 1; });
    setCallback(() => { second += 1; });
    unregisterFirst(); // stale handle: must not remove the newer callback
    setAccessToken(TOKEN_A);
    installFetch(async () => fakeResponse(401, CANARY + " token=" + TOKEN_A));
    await assert.rejects(apiGet("/api/cases"), (e: unknown) => expectApiError(e, 401, /Sign-in required/));
    assert.equal(first, 0);
    assert.equal(second, 1, "current callback must fire exactly once");
    const seen = installFetch(async () => fakeResponse(200, "null"));
    await apiGet("/api/cases");
    assert.equal(authOf(seen[0]), null, "401 must clear token until explicit sign-in");
    setAccessToken(TOKEN_B);
    const seen2 = installFetch(async () => fakeResponse(200, "null"));
    await apiGet("/api/cases");
    assert.equal(authOf(seen2[0]), `Bearer ${TOKEN_B}`);
    assert.equal(second, 1, "no repeat callback without a new 401");
  }

  // 4) Sanitized failures: network throw, body-read throw, malformed JSON,
  //    and 403/409/429/503. Exactly one request each, no automatic retry,
  //    no raw server/upstream text, failure never manufactures success.
  {
    resetSession();
    setAccessToken(TOKEN_A);
    let seen = installFetch(async () => { throw new Error(CANARY_UPSTREAM); });
    await assert.rejects(apiGet("/api/cases"), (e: unknown) => expectApiError(e, 0, /Cannot reach/));
    assert.equal(seen.length, 1, "network failure must not retry");

    seen = installFetch(async () => ({
      ok: true,
      status: 200,
      text: async () => { throw new Error(CANARY_UPSTREAM); },
    }));
    await assert.rejects(apiGet("/api/cases"), (e: unknown) => expectApiError(e, 0, /unreadable/));
    assert.equal(seen.length, 1);

    seen = installFetch(async () => fakeResponse(200, "{" + CANARY));
    await assert.rejects(apiGet("/api/cases"), (e: unknown) => expectApiError(e, 0, /unreadable/));
    assert.equal(seen.length, 1, "malformed JSON must not retry or succeed");

    const statuses: Array<[number, RegExp]> = [
      [403, /not permitted/],
      [409, /data changed[\s\S]*fresh investigation/i], // API message only; not React stale-state QA
      [429, /Too many requests/],
      [503, /currently unavailable/],
    ];
    for (const [status, pattern] of statuses) {
      seen = installFetch(async () => fakeResponse(status, CANARY + " " + CANARY_UPSTREAM));
      await assert.rejects(
        apiPost("/api/preview", { synthetic: true }),
        (e: unknown) => expectApiError(e, status, pattern),
      );
      assert.equal(seen.length, 1, `status ${status} must not retry`);
    }
  }

  // 5) Path containment, rejected before fetch/credential attachment, plus
  //    an allowed ordinary query. Key-shape boundaries.
  {
    resetSession();
    setAccessToken(TOKEN_A);
    const badPaths: string[] = [
      "https://example.invalid/api/cases",
      "http://example.invalid/api/cases",
      "//example.invalid/api/cases",
      "/outside",
      "/api",
      "/apis/cases",
      "api/cases",
      "",
      "/api/../outside",
      "/api/%2e%2e/outside",
      "/api/%2E%2E/outside",
      "/api\\cases",
      "/api/cases\\..\\outside",
      "/api/ca ses",
      "/api/cases\t",
      "/api/cases\n",
      "/api/cases\r\n",
      " /api/cases",
      "/api/cases\u0001",
      "/api/./cases",
    ];
    for (const path of badPaths) {
      const seen = installFetch(async () => fakeResponse(200, "null"));
      await assert.rejects(
        apiGet(path),
        (e: unknown) => expectApiError(e, 0, /not permitted/),
        `path must be rejected: ${JSON.stringify(path)}`,
      );
      assert.equal(seen.length, 0, `fetch called for rejected path: ${JSON.stringify(path)}`);
    }
    const seen = installFetch(async () => fakeResponse(200, JSON.stringify({ ok: true })));
    const data: any = await apiGet("/api/cases?limit=3&view=full");
    assert.deepEqual(data, { ok: true });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, "/api/cases?limit=3&view=full");
    assert.equal(authOf(seen[0]), `Bearer ${TOKEN_A}`);

    assert.equal(isValidAccessKeyShape("a".repeat(32)), true);
    assert.equal(isValidAccessKeyShape("a".repeat(128)), true);
    assert.equal(isValidAccessKeyShape("AbZ09-_" + "x".repeat(25)), true);
    assert.equal(isValidAccessKeyShape("a".repeat(31)), false);
    assert.equal(isValidAccessKeyShape("a".repeat(129)), false);
    assert.equal(isValidAccessKeyShape("a".repeat(31) + "\n"), false, "final newline invalid");
    assert.equal(isValidAccessKeyShape("a".repeat(31) + "\r"), false, "final CR invalid");
    assert.equal(isValidAccessKeyShape("a".repeat(31) + " "), false, "space invalid");
    assert.equal(isValidAccessKeyShape("a".repeat(31) + "é"), false, "non-ASCII invalid");
    assert.equal(isValidAccessKeyShape("a".repeat(16) + " " + "a".repeat(15)), false);
  }

  // 6) Malformed token causing Headers construction/set failure: sanitized
  //    ApiError(0), no fetch, malformed value never leaked. This bypasses UI
  //    key-shape validation only to test module defense-in-depth.
  {
    resetSession();
    const malformed = "synthetic-bad-token\nwith-newline";
    setAccessToken(malformed);
    const seen = installFetch(async () => fakeResponse(200, "null"));
    await assert.rejects(apiGet("/api/cases"), (e: unknown) => {
      assert.ok(e instanceof ApiError);
      assert.equal((e as ApiError).status, 0);
      assert.ok(!(e as ApiError).message.includes(malformed), "leaked malformed token");
      assert.ok(!(e as ApiError).message.includes("with-newline"), "leaked malformed token fragment");
      return true;
    });
    assert.equal(seen.length, 0, "fetch must not run when header preparation fails");
  }

  console.log("check-api-session: mocked fetch regression assertions passed (native Node, NOT browser/mobile QA)");
}

main().then(
  () => {
    resetSession();
    (globalThis as any).fetch = originalFetch;
  },
  (error: unknown) => {
    resetSession();
    (globalThis as any).fetch = originalFetch;
    throw error;
  },
);
