import { currentStep, formatDuration, formatEta } from "./status";

describe("currentStep", () => {
  it.each([
    ["queued", 0, 0, "read"],
    ["running", 0, 0, "read"],
    ["running", 100, 40, "match"],
    ["paused_client", 0, 0, "read"],
    ["paused_quota", 100, 10, "match"],
    ["paused_client", 100, 0, "write"],
    ["review", 100, 0, "review"],
    ["writing", 100, 0, "write"],
    ["done", 100, 0, "done"],
  ] as const)("%s total=%i pending=%i → %s", (status, total, pending, step) => {
    expect(currentStep(status, total, pending)).toBe(step);
  });
});

describe("formatters", () => {
  it.each([
    [30, "меньше минуты"],
    [240, "≈ 4 мин"],
    [3600, "≈ 1 ч"],
    [4800, "≈ 1 ч 20 мин"],
  ])("eta %i → %s", (seconds, text) => {
    expect(formatEta(seconds)).toBe(text);
  });

  it("duration", () => {
    expect(formatDuration(285_000)).toBe("4:45");
    expect(formatDuration(61_000)).toBe("1:01");
    expect(formatDuration(null)).toBeNull();
  });
});
