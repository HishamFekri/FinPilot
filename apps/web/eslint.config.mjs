import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// RTL works by construction only if layouts use logical properties (ARCHITECTURE.md §14.4).
const PHYSICAL_CLASS =
  "/(^|[\\s:])-?(ml|mr|pl|pr|left|right|border-l|border-r|rounded-l|rounded-r|text-left|text-right|float-left|float-right)(-|\\s|$)/";
const PHYSICAL_MESSAGE =
  "Use logical properties (ms-, me-, ps-, pe-, start-, end-, text-start, text-end), so RTL layouts work.";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/**/*.test.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        { selector: `Literal[value=${PHYSICAL_CLASS}]`, message: PHYSICAL_MESSAGE },
        { selector: `TemplateElement[value.raw=${PHYSICAL_CLASS}]`, message: PHYSICAL_MESSAGE },
      ],
      // Visible text comes from the translation catalogs, not from JSX.
      "react/jsx-no-literals": ["error", { noStrings: false, ignoreProps: true }],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);
