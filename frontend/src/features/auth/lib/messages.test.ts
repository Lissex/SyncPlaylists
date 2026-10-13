import { ApiError } from "@/shared/api";

import { authErrorMessage } from "./messages";

const tooMany = (seconds: number | null) => new ApiError(429, "x", null, seconds);

describe("authErrorMessage", () => {
  it.each([
    [60, "1 минуту"],
    [120, "2 минуты"],
    [300, "5 минут"],
    [1260, "21 минуту"],
    [690, "12 минут"],
  ])("rate limit of %i s → «%s»", (seconds, text) => {
    expect(authErrorMessage(tooMany(seconds), "login")).toContain(text);
  });

  it("rate limit without Retry-After", () => {
    expect(authErrorMessage(tooMany(null), "login")).toBe(
      "Слишком много попыток. Попробуйте позже.",
    );
  });

  it("network error keeps the client message", () => {
    const error = new ApiError(0, "Нет связи с сервером. Проверьте интернет.", "network", null);
    expect(authErrorMessage(error, "login")).toBe("Нет связи с сервером. Проверьте интернет.");
  });
});
