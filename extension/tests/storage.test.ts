import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it } from "vitest";

import { Api } from "../src/background/api";
import { Journal, JOURNAL_TTL_MS } from "../src/background/journal";
import { forgetDevice, readState, writeState } from "../src/background/state";

beforeEach(() => fakeBrowser.reset());

describe("журнал write-операций", () => {
  it("хранит результат сутки, потом забывает", async () => {
    let now = 0;
    const journal = new Journal(() => now);

    await journal.put("create_playlist:r1", "soundcloud.create_playlist", { playlist_id: "p1" });
    expect(await journal.get("create_playlist:r1")).toEqual({ playlist_id: "p1" });

    now = JOURNAL_TTL_MS + 1;
    expect(await journal.get("create_playlist:r1")).toBeNull();
    await journal.prune();
    expect(await fakeBrowser.storage.local.get(null)).toEqual({});
  });

  it("отвязка стирает токен, журнал и outbox, но не чужие ключи", async () => {
    await writeState({ deviceToken: "t", accountEmail: "a@b", deviceId: "d" });
    await new Journal().put("k", "op", { x: 1 });
    await fakeBrowser.storage.local.set({ outbox: [], connection: { status: "online" } });

    await forgetDevice();

    expect(await fakeBrowser.storage.local.get(null)).toEqual({
      connection: { status: "online" },
    });
    expect(await readState("deviceToken")).toEqual({});
  });
});

describe("HTTP привязки", () => {
  function api(responses: Array<[number, unknown]>) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = responses.shift()!;
      return new Response(body === null ? null : JSON.stringify(body), { status });
    }) as unknown as typeof fetch;
    return { api: new Api("http://localhost:8000", fetchImpl), calls };
  }

  it("опрос привязки: pending → paired, pairing_id в теле, без cookie", async () => {
    const { api: client, calls } = api([
      [202, { status: "pending", device_id: null, device_token: null }],
      [200, { status: "paired", device_id: "d1", device_token: "tok-0123456789" }],
      [410, { detail: { code: "pairing_expired" } }],
    ]);

    expect(await client.claimPairing("pairing-secret-1")).toEqual({ status: "pending" });
    expect(await client.claimPairing("pairing-secret-1")).toEqual({
      status: "paired",
      deviceId: "d1",
      deviceToken: "tok-0123456789",
    });
    expect(await client.claimPairing("pairing-secret-1")).toEqual({ status: "expired" });
    expect(calls[0]!.url).toBe("http://localhost:8000/extension/pairings/token");
    expect(calls[0]!.url).not.toContain("pairing-secret-1");
    expect(calls[0]!.init.credentials).toBe("omit");
  });

  it("токен устройства — в заголовке Authorization: Device", async () => {
    const { api: client, calls } = api([
      [200, { device_id: "d1", user_email: "a@b.c" }],
      [401, null],
      [204, null],
    ]);

    expect(await client.me("tok-0123456789")).toEqual({ deviceId: "d1", email: "a@b.c" });
    expect(await client.me("tok-0123456789")).toBeNull();
    await client.unpair("tok-0123456789");
    expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBe(
      "Device tok-0123456789",
    );
    expect(calls[2]!.init.method).toBe("DELETE");
  });
});
