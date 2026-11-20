/* Shared helpers: API calls with CSRF, toasts, confirm dialog, formatting, nav. */
"use strict";

const SC = (() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function api(url, { method = "GET", json, body, headers = {} } = {}) {
    const opts = { method, headers: { "X-CSRFToken": csrf, ...headers }, credentials: "same-origin" };
    if (json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(json);
    } else if (body !== undefined) {
      opts.body = body;
    }
    const r = await fetch(url, opts);
    let data = {};
    try { data = await r.json(); } catch (_) { /* non-JSON response */ }
    if (!r.ok || data.success === false) {
      const err = new Error(data.error || `Request failed (HTTP ${r.status})`);
      err.status = r.status;
      err.data = data;
      if (data.must_change_password) window.location.href = "/account";
      throw err;
    }
    return data;
  }

  function toast(message, type = "info", timeout = 5000) {
    const box = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    const text = document.createElement("span");
    text.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", SC.t ? SC.t("Dismiss") : "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", () => el.remove());
    el.append(text, close);
    box.appendChild(el);
    if (timeout) setTimeout(() => el.remove(), timeout);
  }

  function confirmDialog(message, { title = "Are you sure?", confirmText = "OK", danger = false } = {}) {
    const dlg = document.getElementById("confirm-dialog");
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-message").textContent = message;
    const ok = document.getElementById("confirm-ok");
    ok.textContent = confirmText;
    ok.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    const done = openDialog(dlg);
    dlg.querySelector('button[value="cancel"]').focus();
    return done;
  }

  /* Show a modal <dialog> containing a <form>. Resolves true when the form is submitted (Enter or
     the confirm button), false on Cancel/Escape. Driven by submit/cancel/click events, not
     dialog.returnValue or the "close" event: returnValue is empty after Enter-key submission, and
     a queued "close" from the previous use can land on the next one. */
  function openDialog(dlg) {
    if (dlg._finish) dlg._finish(false);  // a previous use that never settled
    return new Promise((resolve) => {
      const form = dlg.querySelector("form");
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        form.removeEventListener("submit", onSubmit);
        dlg.removeEventListener("cancel", onCancel);
        dlg.removeEventListener("click", onClick);
        dlg._finish = null;
        if (dlg.open) dlg.close();
        resolve(ok);
      };
      const onSubmit = (e) => { e.preventDefault(); finish(true); };
      const onCancel = (e) => { e.preventDefault(); finish(false); };
      const onClick = (e) => { if (e.target.closest('button[value="cancel"]')) finish(false); };
      form.addEventListener("submit", onSubmit);
      dlg.addEventListener("cancel", onCancel);
      dlg.addEventListener("click", onClick);
      dlg._finish = finish;
      dlg.showModal();
    });
  }

  /* Text input dialog (value) or select dialog (options: [{value, label}]). Resolves null on cancel. */
  function promptDialog({ title, label, value = "", options = null, confirmText = "OK" }) {
    const dlg = document.getElementById("prompt-dialog");
    const input = document.getElementById("prompt-input");
    const select = document.getElementById("prompt-select");
    document.getElementById("prompt-title").textContent = title;
    document.getElementById("prompt-label").textContent = label;
    document.getElementById("prompt-ok").textContent = confirmText;
    input.hidden = !!options;
    select.hidden = !options;
    const field = options ? select : input;
    document.getElementById("prompt-label").htmlFor = field.id;
    if (options) {
      select.replaceChildren(...options.map((o) => el("option", { value: o.value, text: o.label })));
      select.value = value;
    } else {
      input.value = value;
    }
    const done = openDialog(dlg).then((ok) => (ok ? field.value : null));
    field.focus();
    if (!options) {
      const dot = value.lastIndexOf(".");
      input.setSelectionRange(0, dot > 0 ? dot : value.length);
    }
    return done;
  }

  function formatBytes(n) {
    if (n === null || n === undefined) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i && n < 10 ? 1 : 0)} ${units[i]}`;
  }

  function formatDate(s) {
    if (!s) return "";
    const d = new Date(s.replace(" ", "T") + (s.endsWith("Z") ? "" : "Z"));
    return d.toLocaleString(document.documentElement.lang || undefined);
  }

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) e.append(c);
    return e;
  }

  async function refreshUsage() {
    const boxes = document.querySelectorAll("[data-usage]");
    if (!boxes.length) return;
    try {
      const u = await api("/api/usage");
      const used = u.used + u.reserved;
      const pct = u.quota ? Math.min(100, Math.round((used / u.quota) * 100)) : 0;
      const T = SC.t || ((x) => x);
      for (const box of boxes) {
        const bar = box.querySelector(".progress");
        bar.setAttribute("aria-valuenow", String(pct));
        bar.hidden = !u.quota;
        bar.classList.toggle("progress-warn", pct >= 90);
        bar.querySelector("span").style.width = `${pct}%`;
        box.querySelector(".usage-text").textContent = u.quota
          ? `${formatBytes(used)} ${T("of")} ${formatBytes(u.quota)} ${T("used")} (${pct}%)`
          : `${formatBytes(used)} ${T("used")} · ${T("no quota")}`;
      }
    } catch (_) { /* usage is informational */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    refreshUsage();
    const toggle = document.querySelector(".nav-toggle");
    const nav = document.getElementById("main-nav");
    toggle?.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      nav.classList.toggle("open", open);
    });
    document.querySelector('[data-action="logout"]')?.addEventListener("click", async () => {
      try {
        const r = await api("/logout", { method: "POST" });
        window.location.href = r.redirect_url;
      } catch (e) {
        window.location.href = "/login";
      }
    });
  });

  return { api, toast, confirm: confirmDialog, prompt: promptDialog, openDialog, formatBytes, formatDate, el, csrf, refreshUsage };
})();
