// Nonvisual tests for lib/api.ts chat(): timeout, 401 refresh-once, auth failure,
// error statuses. Run: npm test (Node's built-in runner; lib/api.ts is transpiled with
// the project's own TypeScript, so no extra dependencies).
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const file = join(mkdtempSync(join(tmpdir(), "tarifeai-api-")), "api.mjs");
writeFileSync(file, js);
const api = await import(file);

const OK = { answer: "Öneri [S1]", citations: [], packages: [] };

function respond(status, body) {
  return new Response(JSON.stringify(body ?? {}), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Replace fetch with a scripted sequence; returns the recorded calls. */
function mockFetch(...steps) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const step = steps[Math.min(calls.length, steps.length) - 1];
    return typeof step === "function" ? step(init) : step;
  };
  return calls;
}

function tokens(...values) {
  const asked = [];
  const provider = async (refresh) => {
    asked.push(refresh);
    return values[Math.min(asked.length, values.length) - 1];
  };
  return { provider, asked };
}

test("success sends the bearer token once", async () => {
  const calls = mockFetch(respond(200, OK));
  const t = tokens("tok-1");
  assert.deepEqual(await api.chat("soru", [], t.provider), OK);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.headers.Authorization, "Bearer tok-1");
  assert.deepEqual(t.asked, [false]);
});

test("401 refreshes once and retries once", async () => {
  const calls = mockFetch(respond(401), respond(200, OK));
  const t = tokens("expired", "fresh");
  assert.deepEqual(await api.chat("soru", [], t.provider), OK);
  assert.deepEqual(t.asked, [false, true]);
  assert.equal(calls[1].init.headers.Authorization, "Bearer fresh");
});

test("refresh failure is an AuthError, without a second request", async () => {
  const calls = mockFetch(respond(401));
  const t = tokens("expired", null);
  await assert.rejects(api.chat("soru", [], t.provider), api.AuthError);
  assert.equal(calls.length, 1);
});

test("a second 401 stops: no refresh loop", async () => {
  const calls = mockFetch(respond(401), respond(401), respond(200, OK));
  const t = tokens("a", "b", "c");
  await assert.rejects(api.chat("soru", [], t.provider), api.AuthError);
  assert.equal(calls.length, 2);
  assert.deepEqual(t.asked, [false, true]);
});

test("quota 429 maps to QuotaError with its reason", async () => {
  mockFetch(respond(429, { detail: { reason: "daily_limit" } }));
  await assert.rejects(api.chat("soru", [], tokens("t").provider), (e) => {
    assert.ok(e instanceof api.QuotaError);
    assert.equal(e.reason, "daily_limit");
    return true;
  });
});

test("provider 503 is an error, never a ChatResponse (no history contamination)", async () => {
  mockFetch(respond(503, { detail: { reason: "provider_unavailable" } }));
  await assert.rejects(api.chat("soru", [], tokens("t").provider), /API error 503/);
});

test("network failure surfaces as TypeError", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(api.chat("soru", [], tokens("t").provider), TypeError);
});

test("a hung request is aborted after CHAT_TIMEOUT_MS", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let signal;
  globalThis.fetch = (url, init) => {
    signal = init.signal;
    return new Promise((_, reject) =>
      init.signal.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError"))
      )
    );
  };
  const pending = api.chat("soru", [], tokens("t").provider);
  await new Promise((r) => setImmediate(r)); // let chat() reach fetch
  t.mock.timers.tick(api.CHAT_TIMEOUT_MS);
  await assert.rejects(pending, api.RequestTimeoutError);
  assert.equal(signal.aborted, true);
  assert.ok(!(new api.RequestTimeoutError() instanceof TypeError)); // not shown as "offline"
});
