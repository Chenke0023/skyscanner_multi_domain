import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

function renderFatal(title: string, detail: string) {
  const root = document.getElementById("root");
  if (!root) {
    return;
  }
  root.innerHTML = `
    <main style="min-height:100vh;display:grid;place-items:center;padding:28px;background:#f7f7f5;color:#20252b;font-family:'Avenir Next','Helvetica Neue','PingFang SC',sans-serif;">
      <section style="width:min(560px,100%);padding:32px 28px;border-radius:16px;border:1px solid rgba(36,47,55,0.12);background:rgba(255,255,255,0.96);box-shadow:0 16px 44px rgba(31,41,51,0.07)">
        <p style="margin:0 0 10px;color:#9b4a3d;letter-spacing:0;text-transform:uppercase;font-size:12px">Desktop Runtime Error</p>
        <h1 style="margin:0;font-size:28px;line-height:1.05">${title}</h1>
        <pre style="margin:14px 0 0;white-space:pre-wrap;word-break:break-word;color:#68717a;font-size:14px;line-height:1.6">${detail}</pre>
      </section>
    </main>
  `;
}

window.addEventListener("error", (event) => {
  const message = event.error instanceof Error
    ? `${event.error.name}: ${event.error.message}\n${event.error.stack ?? ""}`
    : String(event.message ?? "Unknown error");
  renderFatal("前端启动失败", message);
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason instanceof Error
    ? `${event.reason.name}: ${event.reason.message}\n${event.reason.stack ?? ""}`
    : String(event.reason ?? "Unhandled promise rejection");
  renderFatal("前端启动失败", reason);
});

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing #root element in desktop shell.");
}

try {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
} catch (error) {
  const detail = error instanceof Error
    ? `${error.name}: ${error.message}\n${error.stack ?? ""}`
    : String(error);
  renderFatal("前端启动失败", detail);
}
