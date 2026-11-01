import { afterEach, describe, expect, it, vi } from "vitest";
import {
  assignNumber, fetchToken, getGreeting, getMainNumber, getTopQuestions,
  listBusinesses, postChat, saveGreeting, setMainNumber,
} from "./api";

function mockFetch(status: number, body: unknown, statusText = "") {
  const fn = vi.fn(async () => ({
    ok: status >= 200 && status < 300, status, statusText,
    json: async () => { if (body instanceof Error) throw body; return body; },
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("postChat", () => {
  it("POSTs the payload as JSON and returns the reply", async () => {
    const f = mockFetch(200, { answer: "hi", conversation_id: "c1", sources: [] });
    const payload = { business_id: "b", question: "q", conversation_id: null };
    expect((await postChat(payload)).answer).toBe("hi");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/chat");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("throws with status and text on an HTTP error", async () => {
    mockFetch(500, {}, "Server Error");
    await expect(postChat({ business_id: "b", question: "q", conversation_id: null })).rejects.toThrow("HTTP 500: Server Error");
  });
});

describe("GET helpers", () => {
  it("listBusinesses / getMainNumber return the parsed JSON", async () => {
    mockFetch(200, [{ id: "b" }]);
    expect(await listBusinesses()).toEqual([{ id: "b" }]);
    mockFetch(200, { number: "+1555" });
    expect(await getMainNumber()).toEqual({ number: "+1555" });
  });

  it("a failed GET throws HTTP status", async () => {
    mockFetch(404, {}, "Not Found");
    await expect(listBusinesses()).rejects.toThrow("HTTP 404: Not Found");
  });

  it("encodes the business id and passes limit and days (with defaults)", async () => {
    const f = mockFetch(200, {});
    await getGreeting("a/b");
    await getTopQuestions("x y");
    await getTopQuestions("x", 3, 7);
    const urls = f.mock.calls.map((c) => (c as unknown as [string])[0]);
    expect(urls).toEqual([
      "/businesses/a%2Fb/greeting",
      "/businesses/x%20y/top-questions?limit=5&days=30",
      "/businesses/x/top-questions?limit=3&days=7",
    ]);
  });
});

describe("JSON writers", () => {
  it("saveGreeting PUTs the text", async () => {
    const f = mockFetch(200, { text: "hello" });
    await saveGreeting("b", "hello");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/businesses/b/greeting");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ text: "hello" });
  });

  it("assignNumber PUTs the number", async () => {
    const f = mockFetch(200, { business_id: "b", phone_numbers: ["+1"], moved_from: [] });
    await assignNumber("b", "+1");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/businesses/b/number");
    expect(JSON.parse(init.body as string)).toEqual({ number: "+1" });
  });

  it("surfaces the server's string detail, else the status", async () => {
    mockFetch(422, { detail: "Not a valid number" });
    await expect(assignNumber("b", "x")).rejects.toThrow("Not a valid number");
    mockFetch(422, { detail: [{ msg: "list detail" }] });
    await expect(assignNumber("b", "x")).rejects.toThrow("HTTP 422");
    mockFetch(502, new Error("not json"));
    await expect(saveGreeting("b", "x")).rejects.toThrow("HTTP 502");
  });
});

describe("setMainNumber", () => {
  it("POSTs to assign and DELETEs to release", async () => {
    const f = mockFetch(200, {});
    await setMainNumber("b", true);
    await setMainNumber("b", false);
    const calls = f.mock.calls.map((c) => c as unknown as [string, RequestInit]);
    expect(calls.map(([u, i]) => [u, i.method])).toEqual([
      ["/businesses/b/main-number", "POST"],
      ["/businesses/b/main-number", "DELETE"],
    ]);
  });

  it("fails with HTTP status when the server refuses", async () => {
    mockFetch(500, {}, "Boom");
    await expect(setMainNumber("b", true)).rejects.toThrow("HTTP 500: Boom");
  });
});

describe("fetchToken", () => {
  it("returns the token", async () => {
    mockFetch(200, { token: "t", identity: "i" });
    expect(await fetchToken()).toEqual({ token: "t", identity: "i" });
  });

  it("explains a refusal with the server's detail, else the status", async () => {
    mockFetch(403, { detail: "Only on localhost" });
    await expect(fetchToken()).rejects.toThrow("Only on localhost");
    mockFetch(500, new Error("no json"));
    await expect(fetchToken()).rejects.toThrow("HTTP 500");
  });
});
