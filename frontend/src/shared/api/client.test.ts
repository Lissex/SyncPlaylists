import { http, HttpResponse } from "msw";

import { apiUrl as at, setupMswServer } from "@/test/server";

import { ApiError, api } from "./client";

const server = setupMswServer();

describe("api client", () => {
  it("returns JSON and sends cookies with the request", async () => {
    let credentials: RequestCredentials | undefined;
    server.use(
      http.get(at("/auth/me"), ({ request }) => {
        credentials = request.credentials;
        return HttpResponse.json({ email: "a@b.c" });
      }),
    );

    await expect(api.get("/auth/me")).resolves.toEqual({ email: "a@b.c" });
    expect(credentials).toBe("include");
  });

  it("maps {code, message} detail to ApiError", async () => {
    server.use(
      http.post(at("/links/resolve"), () =>
        HttpResponse.json(
          { detail: { code: "playlist_not_found", message: "Плейлист не найден или закрыт" } },
          { status: 422 },
        ),
      ),
    );

    const error = await api.post("/links/resolve", { url: "x" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "playlist_not_found",
      message: "Плейлист не найден или закрыт",
    });
  });

  it("keeps string detail and Retry-After", async () => {
    server.use(
      http.post(at("/auth/login"), () =>
        HttpResponse.json(
          { detail: "Слишком много попыток, попробуйте позже" },
          { status: 429, headers: { "Retry-After": "60" } },
        ),
      ),
    );

    await expect(api.post("/auth/login", {})).rejects.toMatchObject({
      status: 429,
      message: "Слишком много попыток, попробуйте позже",
      retryAfter: 60,
    });
  });

  it("treats 204 as empty success", async () => {
    server.use(http.delete(at("/accounts/1"), () => new HttpResponse(null, { status: 204 })));

    await expect(api.delete("/accounts/1")).resolves.toBeUndefined();
  });

  it("reports network failure in Russian", async () => {
    server.use(http.get(at("/health"), () => HttpResponse.error()));

    await expect(api.get("/health")).rejects.toMatchObject({ status: 0, code: "network" });
  });
});
