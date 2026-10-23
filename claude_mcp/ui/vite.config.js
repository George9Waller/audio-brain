// Each component is one self-contained HTML file in dist/, which the server serves as a ui:// resource.
import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [preact(), viteSingleFile()],
  build: { outDir: "dist", emptyOutDir: false, rollupOptions: { input: process.env.INPUT ?? "list.html" } },
});
