import { describe, expect, it } from "vitest";

import { heardText, recognitionNote, type ItemRecognition } from "./recognition";

const heard = {
  title: "Lucid Dreams",
  artist: "Juice WRLD",
  provider: "shazam",
  isrc: "USUG11800685",
  exact: true,
};

describe("recognition", () => {
  it("пока ждёт — «Распознаём»", () => {
    const queued: ItemRecognition = { state: "queued", note: null, heard: null, can_retry: false };
    expect(recognitionNote(queued)).toBe("Распознаём по звуку…");
  });

  it("причина итога понятными словами", () => {
    const altered: ItemRecognition = { state: "final", note: "altered", heard, can_retry: false };
    expect(recognitionNote(altered)).toMatch(/звук изменён/);
    expect(recognitionNote({ ...altered, note: "timeout" })).toBe("не успели распознать");
  });

  it("найдено по звуку и без распознавания — без подписи", () => {
    expect(recognitionNote({ state: "final", note: "exact", heard, can_retry: false })).toBeNull();
    expect(recognitionNote(null)).toBeNull();
  });

  it("что услышали", () => {
    expect(heardText({ state: "final", note: "exact", heard, can_retry: false })).toBe(
      "По звуку: Juice WRLD — Lucid Dreams",
    );
    expect(heardText({ state: "final", note: "no_match", heard: null, can_retry: false })).toBe(
      null,
    );
  });
});
