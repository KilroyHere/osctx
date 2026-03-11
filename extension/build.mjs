import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "fs";

const watch = process.argv.includes("--watch");

const shared = {
  bundle: true,
  target: "chrome110",
  format: "iife",
  outdir: "dist",
  sourcemap: watch ? "inline" : false,
  minify: !watch,
};

const entryPoints = [
  "background.ts",
  "popup.ts",
  "content/chatgpt.ts",
  "content/claude.ts",
  "content/gemini.ts",
];

mkdirSync("dist/content", { recursive: true });

if (watch) {
  const ctx = await esbuild.context({ ...shared, entryPoints });
  await ctx.watch();
  console.log("Watching for changes…");
} else {
  await esbuild.build({ ...shared, entryPoints });
  // Copy static assets
  cpSync("manifest.json", "dist/manifest.json");
  cpSync("popup.html", "dist/popup.html");
  console.log("Build complete → dist/");
}
