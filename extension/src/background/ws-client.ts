// WebSocket к /extension/ws: hello с токеном устройства первым сообщением (не в URL),
// ping раз в heartbeat_seconds из welcome (в Chrome трафик WebSocket держит service
// worker живым), переподключение с экспоненциальной задержкой. Service worker может быть
// остановлен браузером — тогда alarm «ws-keepalive» вызывает ensure() и связь
// поднимается заново (см. background.ts).

import {
  decodeIncoming,
  encodeOutgoing,
  type IncomingMessage,
  type OutgoingMessage,
  type TaskMessage,
} from "../protocol";

// Коды закрытия сервера (presentation/ws.py).
export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_BAD_ORIGIN = 4403;

export type ConnectionState =
  | { status: "unpaired" }
  | { status: "connecting" }
  | { status: "online"; deviceId: string }
  | { status: "offline"; retryAt: number }
  // Сервер не принимает это расширение (Origin не в EXTENSION__ALLOWED_EXTENSION_IDS).
  | { status: "rejected" };

export interface SocketLike {
  readonly readyState: number;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number): void;
}

export interface WsClientDeps {
  url: string;
  version: string;
  makeSocket(url: string): SocketLike;
  getToken(): Promise<string | null>;
  // Токен отозван (4401) — расширение больше не привязано.
  onRevoked(): Promise<void>;
  onState(state: ConnectionState): void;
  onWelcome(): void;
  onTask(task: TaskMessage): void;
  onMessage?(message: IncomingMessage): void;
  random?: () => number;
  now?: () => number;
}

const OPEN = 1;
const MAX_DELAY_MS = 60_000;
const STABLE_AFTER_MS = 30_000;

export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(MAX_DELAY_MS, 1000 * 2 ** attempt);
  return Math.round(base * (0.8 + 0.4 * random())); // ±20 %
}

export class WsClient {
  private socket: SocketLike | null = null;
  private welcomed = false;
  private attempt = 0;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private reconnect: ReturnType<typeof setTimeout> | null = null;
  private stable: ReturnType<typeof setTimeout> | null = null;
  // Без переподключений: отвязано, отклонено сервером или stop().
  private halted = false;

  constructor(private readonly deps: WsClientDeps) {}

  get online(): boolean {
    return this.welcomed && this.socket?.readyState === OPEN;
  }

  // Подключиться, если не подключены и не ждём переподключения (alarm, старт SW, привязка).
  async ensure(): Promise<void> {
    if (this.socket || this.reconnect || this.halted) return;
    await this.connect();
  }

  // Принудительно: после привязки или по кнопке в popup.
  async restart(): Promise<void> {
    this.stop();
    this.halted = false;
    this.attempt = 0;
    await this.connect();
  }

  stop(): void {
    this.halted = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    this.welcomed = false;
    if (socket) {
      socket.onclose = null;
      socket.close(1000);
    }
  }

  send(message: OutgoingMessage): boolean {
    if (!this.online || !this.socket) return false;
    this.socket.send(encodeOutgoing(message));
    return true;
  }

  private async connect(): Promise<void> {
    const token = await this.deps.getToken();
    if (!token) {
      this.deps.onState({ status: "unpaired" });
      return;
    }
    if (this.socket || this.halted) return;
    this.deps.onState({ status: "connecting" });
    const socket = this.deps.makeSocket(this.deps.url);
    this.socket = socket;
    socket.onopen = () => {
      socket.send(encodeOutgoing({ type: "hello", token, version: this.deps.version }));
    };
    socket.onmessage = (event) => {
      if (typeof event.data === "string") this.onMessage(event.data);
    };
    socket.onerror = () => {
      // Подробности будут в onclose; текст ошибки сокета не нужен.
    };
    socket.onclose = (event) => {
      void this.onClose(socket, event.code);
    };
  }

  private onMessage(raw: string): void {
    const message = decodeIncoming(raw);
    if (!message) return; // неизвестное сообщение — игнорируем (протокол строгий)
    if (message.type === "welcome") {
      this.welcomed = true;
      this.startHeartbeat(message.heartbeat_seconds * 1000);
      this.stable = setTimeout(() => {
        this.attempt = 0;
      }, STABLE_AFTER_MS);
      this.deps.onState({ status: "online", deviceId: message.device_id });
      this.deps.onWelcome();
    } else if (message.type === "task") {
      this.deps.onTask(message);
    }
    this.deps.onMessage?.(message);
  }

  private startHeartbeat(intervalMs: number): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = setInterval(() => this.send({ type: "ping" }), intervalMs);
  }

  private clearTimers(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    if (this.reconnect) clearTimeout(this.reconnect);
    if (this.stable) clearTimeout(this.stable);
    this.heartbeat = this.reconnect = this.stable = null;
  }

  private async onClose(socket: SocketLike, code: number): Promise<void> {
    if (this.socket !== socket) return;
    this.socket = null;
    this.welcomed = false;
    this.clearTimers();
    if (code === CLOSE_UNAUTHORIZED) {
      this.halted = true;
      await this.deps.onRevoked();
      this.deps.onState({ status: "unpaired" });
      return;
    }
    if (code === CLOSE_BAD_ORIGIN) {
      this.halted = true;
      this.deps.onState({ status: "rejected" });
      return;
    }
    if (this.halted) return;
    const delay = backoffDelay(this.attempt, this.deps.random);
    this.attempt += 1;
    const now = this.deps.now ?? Date.now;
    this.deps.onState({ status: "offline", retryAt: now() + delay });
    this.reconnect = setTimeout(() => {
      this.reconnect = null;
      void this.connect();
    }, delay);
  }
}
