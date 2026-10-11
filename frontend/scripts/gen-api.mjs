// Типы API из OpenAPI бэкенда: схема выгружается без Postgres/Redis
// (backend/tests/tools/export_openapi.py), затем openapi-typescript → schema.d.ts.
// Обе выгрузки коммитятся: сборка фронта (и Docker) не зависит от бэкенда.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("..", import.meta.url));
const backend = fileURLToPath(new URL("../../backend", import.meta.url));
const run = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: "inherit", shell: true });

run(
  "uv",
  ["run", "python", "-m", "tests.tools.export_openapi", "../frontend/openapi.json"],
  backend,
);
run(
  "pnpm",
  ["exec", "openapi-typescript", "openapi.json", "-o", "src/shared/api/schema.d.ts"],
  frontend,
);
