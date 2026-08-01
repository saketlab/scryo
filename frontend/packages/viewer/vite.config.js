import { svelte } from "@sveltejs/vite-plugin-svelte";
import icons from "unplugin-icons/vite";
import { defineConfig } from "vite";
import wasm from "vite-plugin-wasm";

import { tsJsonSchemaPlugin } from "./scripts/vite-plugin-ts-json-schema.js";

// https://vitejs.dev/config/
export default defineConfig({
  base: "",
  plugins: [svelte(), wasm(), icons({ compiler: "svelte" }), tsJsonSchemaPlugin()],
  worker: {
    format: "es",
    plugins: () => [wasm()],
    rolldownOptions: {
      output: {
        // stable names; the prebuilt bundle is committed to src/scryo/static/
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name].[ext]",
        codeSplitting: false,
      },
    },
  },
  build: {
    target: "esnext",
    chunkSizeWarningLimit: 4096,
    rolldownOptions: {
      output: {
        // hash only the app JS so a redeploy busts browser and CDN caches;
        // index.html is served no-cache and references the current hash
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name].[ext]",
      },
    },
  },
});
