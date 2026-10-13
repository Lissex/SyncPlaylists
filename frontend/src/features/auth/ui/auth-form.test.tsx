import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { sessionKeys } from "@/entities/session";
import { renderWithClient } from "@/test/render";
import { apiUrl, setupMswServer } from "@/test/server";

import { AuthForm } from "./auth-form";

const server = setupMswServer();
const user = { id: "u1", email: "me@example.com", created_at: "2026-10-11T00:00:00Z" };

async function fill(email: string, password: string) {
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Пароль"), password);
}

describe("AuthForm", () => {
  it("logs in, caches the user and reports success", async () => {
    server.use(http.post(apiUrl("/auth/login"), () => HttpResponse.json(user)));
    const onSuccess = vi.fn();
    const { client } = renderWithClient(<AuthForm mode="login" onSuccess={onSuccess} />);

    await fill("me@example.com", "secret-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
    expect(client.getQueryData(sessionKeys.me)).toEqual(user);
  });

  it("shows a clear message for wrong credentials", async () => {
    server.use(
      http.post(apiUrl("/auth/login"), () =>
        HttpResponse.json({ detail: "Неверный email или пароль" }, { status: 401 }),
      ),
    );
    renderWithClient(<AuthForm mode="login" onSuccess={vi.fn()} />);

    await fill("me@example.com", "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный email или пароль");
  });

  it("checks password length before registering", async () => {
    const onSuccess = vi.fn();
    renderWithClient(<AuthForm mode="register" onSuccess={onSuccess} />);

    await fill("me@example.com", "short");
    await userEvent.click(screen.getByRole("button", { name: "Создать аккаунт" }));

    expect(screen.getByRole("alert")).toHaveTextContent("не короче 8 символов");
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("explains an already registered email", async () => {
    server.use(
      http.post(apiUrl("/auth/register"), () =>
        HttpResponse.json({ detail: "Email уже зарегистрирован" }, { status: 409 }),
      ),
    );
    renderWithClient(<AuthForm mode="register" onSuccess={vi.fn()} />);

    await fill("me@example.com", "long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: "Создать аккаунт" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("уже зарегистрирован");
  });
});
