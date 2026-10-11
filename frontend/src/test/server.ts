import { setupServer } from "msw/node";

/** msw-сервер на файл тестов: обработчики — через server.use() в самом тесте. */
export function setupMswServer() {
  const server = setupServer();
  beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
  return server;
}

export const apiUrl = (path: string) => `${window.location.origin}/api${path}`;
