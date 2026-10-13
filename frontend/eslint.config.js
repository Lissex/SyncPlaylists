import js from "@eslint/js";
import boundaries from "eslint-plugin-boundaries";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Слои Feature-Sliced Design: импорт разрешён только в нижележащие слои
// (app → pages → widgets → features → entities → shared), между слайсами одного слоя — нет.
const LAYERS = ["app", "pages", "widgets", "features", "entities", "shared"];
const below = (layer) => LAYERS.slice(LAYERS.indexOf(layer) + 1);

export default tseslint.config(
  { ignores: ["dist", "src/app/routeTree.gen.ts", "src/shared/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks, boundaries },
    settings: {
      "boundaries/elements": [
        { type: "app", pattern: "src/app" },
        { type: "pages", pattern: "src/pages/*", capture: ["slice"] },
        { type: "widgets", pattern: "src/widgets/*", capture: ["slice"] },
        { type: "features", pattern: "src/features/*", capture: ["slice"] },
        { type: "entities", pattern: "src/entities/*", capture: ["slice"] },
        { type: "shared", pattern: "src/shared" },
      ],
      "import/resolver": { typescript: true },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "boundaries/dependencies": [
        "error",
        {
          default: "disallow",
          policies: LAYERS.map((layer) => ({
            from: { element: { type: layer } },
            allow: [
              ...below(layer).map((type) => ({ to: { element: { type } } })),
              // Внутри своего слайса; у app и shared слайсов нет — весь слой свой.
              layer === "app" || layer === "shared"
                ? { to: { element: { type: layer } } }
                : {
                    to: {
                      element: { type: layer, captured: { slice: "{{from.captured.slice}}" } },
                    },
                  },
            ],
          })),
        },
      ],
    },
  },
);
