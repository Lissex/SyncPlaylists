// HTTP к API SyncPlaylists: привязка (device flow) и сведения об устройстве. Ответы —
// строгие схемы; токен устройства — только в заголовке Authorization: Device.

import { z } from "zod";

const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`${status} ${code}`);
  }
}

const startedSchema = z.strictObject({
  pairing_id: z.string().min(10).max(100),
  user_code: z.string().min(4).max(20),
  expires_in: z.number().int().positive(),
  interval: z.number().int().positive(),
});
export type PairingStarted = z.infer<typeof startedSchema>;

const claimSchema = z.strictObject({
  status: z.enum(["pending", "paired"]),
  device_id: z.string().max(64).nullable(),
  device_token: z.string().max(200).nullable(),
});

const meSchema = z.strictObject({
  device_id: z.string().max(64),
  user_email: z.string().max(320),
});

export type ClaimResult =
  | { status: "pending" }
  | { status: "paired"; deviceId: string; deviceToken: string }
  | { status: "expired" };

export class Api {
  constructor(
    private readonly base: string,
    private readonly fetchImpl: typeof fetch = (...args) => fetch(...args),
  ) {}

  private async request(
    method: string,
    path: string,
    options: { body?: unknown; token?: string } = {},
  ): Promise<{ status: number; json: unknown }> {
    const headers: Record<string, string> = {};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.token) headers.Authorization = `Device ${options.token}`;
    const response = await this.fetchImpl(`${this.base}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      // Cookie сайта SyncPlaylists расширению не нужны: оно ходит только с токеном устройства.
      credentials: "omit",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const text = await response.text();
    let json: unknown = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    return { status: response.status, json };
  }

  async startPairing(device: {
    device_name: string;
    browser: string;
    version: string;
  }): Promise<PairingStarted> {
    const { status, json } = await this.request("POST", "/extension/pairings", { body: device });
    if (status !== 201) throw new ApiError(status, "pairing_failed");
    return startedSchema.parse(json);
  }

  async claimPairing(pairingId: string): Promise<ClaimResult> {
    const { status, json } = await this.request("POST", "/extension/pairings/token", {
      body: { pairing_id: pairingId },
    });
    if (status === 410) return { status: "expired" };
    if (status !== 200 && status !== 202) throw new ApiError(status, "claim_failed");
    const claim = claimSchema.parse({ device_id: null, device_token: null, ...(json as object) });
    if (claim.status === "pending" || !claim.device_id || !claim.device_token) {
      return { status: "pending" };
    }
    return { status: "paired", deviceId: claim.device_id, deviceToken: claim.device_token };
  }

  // null — токен больше не принимается (отозван).
  async me(token: string): Promise<{ deviceId: string; email: string } | null> {
    const { status, json } = await this.request("GET", "/extension/me", { token });
    if (status === 401) return null;
    if (status !== 200) throw new ApiError(status, "me_failed");
    const me = meSchema.parse(json);
    return { deviceId: me.device_id, email: me.user_email };
  }

  async unpair(token: string): Promise<void> {
    const { status } = await this.request("DELETE", "/extension/me", { token });
    // 401 — уже отозвано (например, с сайта): цель достигнута.
    if (status !== 204 && status !== 401) throw new ApiError(status, "unpair_failed");
  }
}
