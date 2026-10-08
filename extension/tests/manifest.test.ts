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

  it("тестовая операция не попадает в сборку без WXT_DIAGNOSTICS", () => {
    expect([...buildRegistry({ apiBase: OPTIONS.apiBase, diagnostics: false }).keys()]).toEqual([]);
    expect([...buildRegistry({ apiBase: OPTIONS.apiBase, diagnostics: true }).keys()]).toEqual([
      "diagnostics.echo",
    ]);
  });
});
