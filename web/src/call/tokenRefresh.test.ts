import { describe, expect, it, vi } from "vitest";
import { keepTokenFresh, type TokenDevice } from "./tokenRefresh";

function fakeDevice() {
  let handler: (() => void) | undefined;
  const device: TokenDevice = {
    on: vi.fn((event: string, listener: () => void) => {
      if (event === "tokenWillExpire") handler = listener;
    }),
    updateToken: vi.fn(),
  };
  return { device, fire: () => handler?.() };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("keepTokenFresh", () => {
  it("fetches a new token and passes it to updateToken on tokenWillExpire", async () => {
    const { device, fire } = fakeDevice();
    const log = vi.fn();
    keepTokenFresh(device, async () => ({ token: "new-token" }), log);
    expect(device.updateToken).not.toHaveBeenCalled();
    fire();
    await flush();
    expect(device.updateToken).toHaveBeenCalledWith("new-token");
    expect(log).toHaveBeenCalledWith("Voice token refreshed.");
  });

  it("logs and does not throw when the fetch fails", async () => {
    const { device, fire } = fakeDevice();
    const log = vi.fn();
    keepTokenFresh(device, async () => Promise.reject(new Error("HTTP 403")), log);
    fire();
    await flush();
    expect(device.updateToken).not.toHaveBeenCalled();
    expect(log).toHaveBeenCalledWith("Could not refresh the Voice token: HTTP 403");
  });
});
