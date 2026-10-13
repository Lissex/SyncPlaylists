import { buildStartRequest, type DestinationChoice } from "./request";

const destination = (overrides: Partial<DestinationChoice> = {}): DestinationChoice => ({
  platform: "vk",
  accountId: "acc-vk",
  kind: "new",
  title: "Летний вайб",
  url: "",
  ...overrides,
});

describe("buildStartRequest", () => {
  it("link source → new playlist", () => {
    expect(
      buildStartRequest({ kind: "link", url: "  https://music.yandex.ru/x  " }, destination()),
    ).toEqual({
      source: { kind: "link", url: "https://music.yandex.ru/x" },
      destination: { kind: "new", platform: "vk", title: "Летний вайб" },
      recognize_audio: true,
    });
  });

  it("recognition by sound can be turned off", () => {
    expect(
      buildStartRequest({ kind: "link", url: "https://a" }, destination(), false)?.recognize_audio,
    ).toBe(false);
  });

  it("library source → library destination", () => {
    expect(
      buildStartRequest(
        { kind: "library", platform: "yandex", accountId: "acc-ya" },
        destination({ kind: "library" }),
      ),
    ).toEqual({
      source: { kind: "library", platform: "yandex", account_id: "acc-ya" },
      destination: { kind: "library", platform: "vk", account_id: "acc-vk" },
      recognize_audio: true,
    });
  });

  it("existing playlist is sent as a link", () => {
    const request = buildStartRequest(
      { kind: "link", url: "https://a" },
      destination({ kind: "existing", url: " https://vk.com/music/playlist/1_2 " }),
    );
    expect(request?.destination).toEqual({
      kind: "link",
      url: "https://vk.com/music/playlist/1_2",
    });
  });

  it.each([
    ["no source", null, destination()],
    ["empty title", { kind: "link", url: "https://a" } as const, destination({ title: "  " })],
    [
      "too long title",
      { kind: "link", url: "https://a" } as const,
      destination({ title: "x".repeat(101) }),
    ],
    [
      "no existing link",
      { kind: "link", url: "https://a" } as const,
      destination({ kind: "existing", url: "" }),
    ],
  ])("incomplete: %s", (_, source, dest) => {
    expect(buildStartRequest(source, dest)).toBeNull();
  });
});
