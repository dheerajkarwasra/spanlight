import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts", "src/otel.ts"],
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  target: "node22",
});
