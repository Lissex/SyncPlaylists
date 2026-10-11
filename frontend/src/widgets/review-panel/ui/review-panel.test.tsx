import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import type { Transfer, TransferItem } from "@/entities/transfer";
import { renderWithClient } from "@/test/render";
import { apiUrl, setupMswServer } from "@/test/server";

import { durationDelta } from "../lib/duration";
import { ReviewPanel } from "./review-panel";

const server = setupMswServer();

const candidate = (id: string, title: string, durationMs: number) => ({
  platform: "vk" as const,
  external_id: id,
  title,
  artist: "Кино",
  duration_ms: durationMs,
  isrc: null,
  artists: [],
  cover_url: null,
  uploader: null,
  rights_holder: false,
  restriction: null,
});

const items: TransferItem[] = [
  {
    position: 0,
    status: "uncertain",
    source_platform: "yandex",
    source_external_id: "y1",
    source_title: "Группа крови",
    source_artist: "Кино",
    source_duration_ms: 285_000,
    match: null,
    candidates: [
      candidate("v1", "Группа крови (Live)", 340_000),
      candidate("v2", "Группа крови", 287_000),
    ],
  },
  {
    position: 1,
    status: "not_found",
    source_platform: "yandex",
    source_external_id: "y2",
    source_title: "Редкий трек",
    source_artist: "Неизвестный",
    source_duration_ms: 200_000,
    match: null,
    candidates: [],
  },
];

const transfer = {
  id: "t-1",
  user_id: "u",
  status: "review",
  source: { kind: "playlist", platform: "yandex", external_id: "p" },
  destination: { kind: "new", platform: "vk", title: "Летний вайб", description: null },
  items: [],
  resolved_targets: [],
  match_methods: {},
  progress: {
    status: "review",
    total: 10,
    pending: 0,
    matched: 8,
    uncertain: 1,
    not_found: 1,
    added: 0,
    failed: 0,
    skipped: 0,
    eta_seconds: null,
    resume_at: null,
    pause_reason: null,
  },
} as unknown as Transfer;

describe("ReviewPanel", () => {
  it("chooses a candidate, keeps the track visible, skips, then starts writing", async () => {
    const resolved: unknown[] = [];
    let wrote = false;
    server.use(
      http.get(apiUrl("/transfers/t-1/items"), () =>
        HttpResponse.json({ items, next_after: null }),
      ),
      http.post(apiUrl("/transfers/t-1/items/:position/resolve"), async ({ request, params }) => {
        resolved.push({ position: params.position, body: await request.json() });
        return new HttpResponse(null, { status: 204 });
      }),
      http.post(apiUrl("/transfers/t-1/write"), () => {
        wrote = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithClient(<ReviewPanel transfer={transfer} />);

    const first = await screen.findByRole("article", { name: "Кино — Группа крови" });
    // Длительность второго кандидата почти совпадает, первого — сильно больше (live).
    expect(within(first).getByText("(+0:02)")).toBeInTheDocument();
    expect(within(first).getByText("(+0:55)")).toHaveClass("text-warning");

    await userEvent.click(within(first).getAllByRole("button", { name: "Выбрать" })[1]!);
    await waitFor(() => expect(within(first).getByText("Выбрано")).toBeInTheDocument());
    expect(within(first).getByText("Выбран")).toBeInTheDocument();

    const second = screen.getByRole("article", { name: "Неизвестный — Редкий трек" });
    expect(within(second).getByText(/ничего похожего не нашлось/)).toBeInTheDocument();
    await userEvent.click(within(second).getByRole("button", { name: "Пропустить" }));
    await waitFor(() => expect(within(second).getByText("Пропущено")).toBeInTheDocument());

    expect(resolved).toEqual([
      { position: "0", body: { chosen_platform: "vk", chosen_external_id: "v2" } },
      { position: "1", body: {} },
    ]);

    await userEvent.click(screen.getByRole("button", { name: "Записать в VK Музыка" }));
    await waitFor(() => expect(wrote).toBe(true));
  });
});

describe("durationDelta", () => {
  it("formats and flags large differences", () => {
    expect(durationDelta(287_000, 285_000)).toEqual({ text: "+0:02", large: false });
    expect(durationDelta(200_000, 285_000)).toEqual({ text: "−1:25", large: true });
    expect(durationDelta(null, 285_000)).toBeNull();
  });
});
