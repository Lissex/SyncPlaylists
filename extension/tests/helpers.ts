import type { SocketLike } from "../src/background/ws-client";
import { decodeIncoming } from "../src/protocol";

// WebSocket для тестов: всё отправленное — в sent, сообщения сервера — через receive().
export class FakeSocket implements SocketLike {
  readyState = 0;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  readonly sent: string[] = [];
  closedWith: number | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = 3;
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(message: unknown): void {
    const raw = JSON.stringify(message);
    if (decodeIncoming(raw) === null) throw new Error(`тест шлёт невалидное сообщение: ${raw}`);
    this.onmessage?.({ data: raw });
  }

  serverClose(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }

  messages(): Array<Record<string, unknown>> {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

export const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
