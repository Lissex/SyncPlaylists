import type { Transfer } from "@/entities/transfer";

import { pauseInfo } from "./pause";

const transfer = (progress: Partial<NonNullable<Transfer["progress"]>>): Transfer =>
  ({
    id: "t",
    user_id: "u",
    status: progress.status ?? "running",
    source: { kind: "playlist", platform: "yandex", external_id: "1" },
    destination: { kind: "new", platform: "vk", title: "x", description: null },
    items: [],
    resolved_targets: [],
    match_methods: {},
    recognize_audio: true,
    progress: {
      status: "running",
      total: 10,
      pending: 5,
      matched: 5,
      uncertain: 0,
      not_found: 0,
      added: 0,
      failed: 0,
      skipped: 0,
      eta_seconds: null,
      resume_at: null,
      pause_reason: null,
      recognizing: 0,
      ...progress,
    },
  }) as Transfer;

describe("pauseInfo", () => {
  it("is null while running", () => {
    expect(pauseInfo(transfer({}))).toBeNull();
  });

  it("closed browser before reading names the source site", () => {
    const info = pauseInfo(
      transfer({ status: "paused_client", pause_reason: "logged_out", total: 0 }),
    );
    expect(info?.text).toContain("music.yandex.ru");
  });

  it("captcha while writing names the destination site", () => {
    const info = pauseInfo(
      transfer({ status: "paused_client", pause_reason: "captcha", total: 10 }),
    );
    expect(info?.text).toContain("vk.com");
  });

  it("VK daily limit says when it resumes", () => {
    const now = new Date("2026-10-11T20:00:00");
    const info = pauseInfo(
      transfer({
        status: "paused_quota",
        pause_reason: "vk_library_daily_limit",
        resume_at: new Date("2026-10-12T09:30:00").toISOString(),
      }),
      now,
    );
    expect(info?.title).toBe("Дневной лимит VK");
    expect(info?.text).toContain("завтра в 09:30");
  });

  it("quota today", () => {
    const now = new Date("2026-10-11T10:00:00");
    const info = pauseInfo(
      transfer({
        status: "paused_quota",
        resume_at: new Date("2026-10-11T10:15:00").toISOString(),
      }),
      now,
    );
    expect(info?.text).toContain("в 10:15");
    expect(info?.text).not.toContain("завтра");
  });
});
