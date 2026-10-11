import { safeRedirect } from "./redirect";

describe("safeRedirect", () => {
  it.each(["/", "/transfers", "/transfers/abc?tab=review"])("keeps internal path %s", (path) => {
    expect(safeRedirect(path)).toBe(path);
  });

  it.each(["//evil.example", "/\\evil.example", "https://evil.example", "transfers", 42, null])(
    "drops unsafe value %s",
    (value) => {
      expect(safeRedirect(value)).toBeUndefined();
    },
  );
});
