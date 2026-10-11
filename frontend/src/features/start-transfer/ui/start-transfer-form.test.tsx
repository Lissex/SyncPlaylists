import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { renderWithClient } from "@/test/render";
import { apiUrl, setupMswServer } from "@/test/server";

import { StartTransferForm } from "./start-transfer-form";

const server = setupMswServer();

const account = (platform: string, id: string) => ({
  id,
  platform,
  transport: "extension",
  status: "active",
  external_user_id: `${platform}-uid`,
  display_name: null,
  expires_at: null,
  connected_at: "2026-10-01T00:00:00Z",
});

const LINK = "https://music.yandex.ru/users/me/playlists/3";

function mockApi(onStart: (body: unknown) => void) {
  server.use(
    http.get(apiUrl("/accounts"), () =>
      HttpResponse.json([account("yandex", "acc-ya"), account("vk", "acc-vk")]),
    ),
    http.post(apiUrl("/links/resolve"), () =>
      HttpResponse.json({
        platform: "yandex",
        kind: "playlist",
        external_id: "me:3",
        account_id: null,
        title: "Летний вайб",
        track_count: 248,
      }),
    ),
    http.post(apiUrl("/transfers"), async ({ request }) => {
      onStart(await request.json());
      return HttpResponse.json({ id: "t-1" }, { status: 201 });
    }),
  );
}

describe("StartTransferForm", () => {
  it("previews the link, prefills the title and starts the transfer", async () => {
    let body: unknown;
    mockApi((b) => (body = b));
    const onStarted = vi.fn();
    renderWithClient(<StartTransferForm onStarted={onStarted} />);

    fireEvent.change(await screen.findByLabelText("Ссылка на плейлист"), {
      target: { value: LINK },
    });
    expect(await screen.findByText("Летний вайб")).toBeInTheDocument();
    expect(screen.getByText(/248 треков/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "VK Музыка" }));
    expect(screen.getByLabelText("Название нового плейлиста")).toHaveValue("Летний вайб");

    const start = screen.getByRole("button", { name: "Перенести" });
    await waitFor(() => expect(start).toBeEnabled());
    await userEvent.click(start);

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("t-1"));
    expect(body).toEqual({
      source: { kind: "link", url: LINK },
      destination: { kind: "new", platform: "vk", title: "Летний вайб" },
    });
  });

  it("keeps a title typed by hand", async () => {
    let body: unknown;
    mockApi((b) => (body = b));
    renderWithClient(<StartTransferForm onStarted={vi.fn()} />);

    fireEvent.change(await screen.findByLabelText("Ссылка на плейлист"), {
      target: { value: LINK },
    });
    await screen.findByText("Летний вайб");
    await userEvent.click(screen.getByRole("button", { name: "VK Музыка" }));
    const title = screen.getByLabelText("Название нового плейлиста");
    await userEvent.clear(title);
    await userEvent.type(title, "Мой микс");
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));

    await waitFor(() =>
      expect(body).toMatchObject({ destination: { kind: "new", title: "Мой микс" } }),
    );
  });

  it("can transfer a whole library without a link", async () => {
    let body: unknown;
    mockApi((b) => (body = b));
    renderWithClient(<StartTransferForm onStarted={vi.fn()} />);

    await userEvent.click(
      await screen.findByRole("button", { name: /Моя медиатека · Яндекс Музыка/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "VK Музыка" }));
    await userEvent.click(screen.getByRole("radio", { name: "Медиатека" }));
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));

    await waitFor(() =>
      expect(body).toEqual({
        source: { kind: "library", platform: "yandex", account_id: "acc-ya" },
        destination: { kind: "library", platform: "vk", account_id: "acc-vk" },
      }),
    );
  });
});
