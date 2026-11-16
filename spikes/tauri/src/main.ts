import { listen } from "@tauri-apps/api/event";

// Rust가 줄 개수를 "count" 이벤트로 보낸다.
window.addEventListener("DOMContentLoaded", () => {
  const el = document.querySelector<HTMLElement>("#count");
  listen<number>("count", (e) => {
    if (el) el.textContent = `줄 ${e.payload}개`;
  });
});
