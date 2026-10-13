import { describe, expect, it } from "vitest";

import { buildRegistry } from "../src/background/registry";
import { buildManifest } from "../src/manifest";
import { optionalOrigins, PLATFORMS } from "../src/platforms";

const OPTIONS = { apiBase: "https://api.syncplaylists.app", geckoId: "x@y" };

describe("манифест", () => {
  it.each(["chrome", "firefox"])("%s: минимальные разрешения", (browser) => {
    const manifest = buildManifest({ ...OPTIONS, browser });

    expect(manifest.permissions).toEqual(["storage", "alarms", "scripting"]);
    expect(manifest.host_permissions).toEqual(["https://api.syncplaylists.app/*"]);
    const all = JSON.stringify(manifest);
    for (const banned of ["cookies", "tabs", "webRequest", "content_scripts", "<all_urls>"]) {
      expect(all).not.toContain(`"${banned}"`);
    }
    expect((manifest.description as string).length).toBeLessThanOrEqual(132);
  });

  it("сайты площадок — только optional и только включаемые", () => {
    const manifest = buildManifest({ ...OPTIONS, browser: "chrome" });

    expect(manifest.optional_host_permissions).toEqual(optionalOrigins());
    const unavailable = PLATFORMS.filter((p) => !p.available).flatMap((p) => p.origins);
    for (const origin of unavailable) expect(optionalOrigins()).not.toContain(origin);
  });

  it("firefox: gecko id и версия с world MAIN", () => {
    const manifest = buildManifest({ ...OPTIONS, browser: "firefox" });

    expect(manifest.browser_specific_settings).toMatchObject({
      gecko: { id: "x@y", strict_min_version: "128.0" },
    });
    expect(manifest.minimum_chrome_version).toBeUndefined();
  });

  it("реестр — только операции включаемых площадок, на их сайтах", () => {
    const registry = buildRegistry();
    const soundcloud = [
      "whoami",
      "liked_tracks_page",
      "liked_track_ids_page",
      "playlist",
      "tracks",
      "like",
      "create_playlist",
      "set_playlist_tracks",
    ];
    const yandex = [
      "whoami",
      "search",
      "tracks",
      "playlist_page",
      "library_ids_page",
      "create_playlist",
      "set_description",
      "add_tracks",
      "like_many",
    ];
    const vk = [
      "whoami",
      "search",
      "tracks",
      "playlist_info",
      "playlist_page",
      "library_page",
      "create_playlist",
      "set_playlist_tracks",
      "add_to_library",
    ];
    const sites: Record<string, string> = {
      soundcloud: "https://soundcloud.com/*",
      yandex: "https://music.yandex.ru/*",
      vk: "https://vk.ru/*",
    };

    expect([...registry.keys()].sort()).toEqual(
      [
        ...soundcloud.map((n) => `soundcloud.${n}`),
        ...yandex.map((n) => `yandex.${n}`),
        ...vk.map((n) => `vk.${n}`),
      ].sort(),
    );
    for (const def of registry.values()) {
      const site = sites[def.op.split(".")[0]!];
      expect(def.target.origins).toEqual([site]);
      expect(optionalOrigins()).toEqual(expect.arrayContaining(def.target.origins));
    }
  });
});
