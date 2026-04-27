import fs from "fs";
import path from "path";

const distDir = path.resolve("dist");
const indexPath = path.join(distDir, "index.html");

function escapeInlineTagContent(source, tagName) {
  const closeTagPattern = new RegExp(`</${tagName}`, "gi");
  return source
    .replace(closeTagPattern, () => `<\\/${tagName}`)
    .replace(/<!--/g, () => "<\\!--");
}

let html = fs.readFileSync(indexPath, "utf-8");

// Inline CSS
const cssLinkMatch = html.match(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"[^>]*>/);
if (cssLinkMatch) {
  const cssPath = path.join(distDir, cssLinkMatch[1].replace(/^\.\//, ""));
  const css = escapeInlineTagContent(fs.readFileSync(cssPath, "utf-8"), "style");
  html = html.replace(cssLinkMatch[0], () => `<style>\n${css}\n</style>`);
}

// Inline JS (remove type="module" and crossorigin)
const jsScriptMatch = html.match(/<script[^>]+src="([^"]+)"[^>]*><\/script>/);
if (jsScriptMatch) {
  const jsPath = path.join(distDir, jsScriptMatch[1].replace(/^\.\//, ""));
  const js = escapeInlineTagContent(fs.readFileSync(jsPath, "utf-8"), "script");
  html = html.replace(jsScriptMatch[0], () => `<script>\n${js}\n</script>`);
}

fs.writeFileSync(indexPath, html);
console.log("Inlined JS/CSS into dist/index.html for pywebview compatibility.");
