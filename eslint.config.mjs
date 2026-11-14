// ESLint flat config — scoped to infra/ CDK TypeScript only.
// Angular uses `ng lint` (eslint run separately by the Angular CLI).
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

export default [
  {
    ignores: [
      "**/node_modules/**",
      "**/cdk.out/**",
      "**/*.d.ts",
      "frontend/**",
    ],
  },
  {
    files: ["infra/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: "./infra/tsconfig.json",
        ecmaVersion: 2022,
        sourceType: "module",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_" },
      ],
    },
  },
];
