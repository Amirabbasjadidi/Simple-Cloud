/* Shared helpers: API calls with CSRF, toasts, confirm dialog, formatting, nav. */
"use strict";

const SC = (() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const lang = document.documentElement.lang || "en";
  let dict = {};
  try { dict = JSON.parse(document.getElementById("i18n-data")?.textContent || "{}"); } catch (_) { /* English */ }

  /* Translate an English source string; {name} placeholders are filled from vars. */
  function t(s, vars) {
    let out = dict[s] || s;
    if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
    return out;
  }
  const num = (n, digits = 0) => Number(n).toLocaleString(lang, { maximumFractionDigits: digits });

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
      const err = new Error(data.error ? t(data.error) : `${t("Request failed")} (HTTP ${r.status})`);
      err.status = r.status;
      err.data = data;
      // Never from /account itself: reloading it would loop forever.
      if (data.must_change_password && window.location.pathname !== "/account") window.location.href = "/account";
      throw err;
    }
    return data;
  }

  function toast(message, type = "info", timeout = 5000) {
    const box = document.getElementById("toasts");
    const node = document.createElement("div");
    node.className = `toast toast-${type}`;
    node.setAttribute("role", type === "error" ? "alert" : "status");
    const text = document.createElement("span");
    text.textContent = message;
    const close = el("button", { type: "button", class: "toast-close", "aria-label": t("Dismiss"), onclick: () => node.remove() }, icon("x"));
    node.append(icon(type === "error" ? "alert" : type === "success" ? "check" : "info"), text, close);
    box.appendChild(node);
    if (timeout) setTimeout(() => node.remove(), timeout);
  }

  function confirmDialog(message, { title = t("Are you sure?"), confirmText = t("OK"), danger = false } = {}) {
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
  function promptDialog({ title, label, value = "", options = null, confirmText = t("OK") }) {
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
    return `${num(n, i && n < 10 ? 1 : 0)} ${t(units[i])}`;
  }

  function formatDate(s) {
    if (!s) return "";
    const d = new Date(s.replace(" ", "T") + (s.endsWith("Z") ? "" : "Z"));
    return d.toLocaleString(document.documentElement.lang || undefined);
  }

  /* Icon from the shared sprite (static/icons.svg). */
  function icon(name, cls = "") {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", `icon ${cls}`.trim());
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", `${SPRITE}#${name}`);
    svg.appendChild(use);
    return svg;
  }
  const SPRITE = document.querySelector('link[rel="stylesheet"][href*="app.css"]')?.href.replace(/app\.css.*$/, "icons.svg") || "/static/icons.svg";

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
      const T = t;
      for (const box of boxes) {
        const bar = box.querySelector(".progress");
        bar.setAttribute("aria-valuenow", String(pct));
        bar.hidden = !u.quota;
        bar.classList.toggle("progress-warn", pct >= 90);
        bar.querySelector("span").style.width = `${pct}%`;
        box.querySelector(".usage-text").textContent = u.quota
          ? `${formatBytes(used)} ${T("of")} ${formatBytes(u.quota)} ${T("used")} (${num(pct)}%)`
          : `${formatBytes(used)} ${T("used")} · ${T("no quota")}`;
      }
    } catch (_) { /* usage is informational */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    refreshUsage();
    const toggle = document.querySelector(".nav-toggle");
    const nav = document.getElementById("sidebar");
    toggle?.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      nav.classList.toggle("open", open);
    });

    // Theme toggle: the choice is remembered per browser; theme.js applies it before first paint.
    const root = document.documentElement;
    // CSS swaps the visible icon and label; the accessible name is kept in step here.
    const themeButtons = document.querySelectorAll('[data-action="theme"]');
    const labelThemeButtons = () => themeButtons.forEach((b) => b.setAttribute("aria-label",
      root.dataset.theme === "dark" ? b.dataset.labelLight : b.dataset.labelDark));
    labelThemeButtons();
    themeButtons.forEach((b) => b.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("sc.theme", next); } catch (_) { /* storage blocked */ }
      labelThemeButtons();
    }));

    // <details class="menu"> overflow menus: one open at a time; close on outside click or Escape.
    document.addEventListener("click", (e) => {
      document.querySelectorAll("details.menu[open]").forEach((d) => {
        if (!d.contains(e.target) || e.target.closest(".menu-item")) d.open = false;
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      const d = document.activeElement?.closest("details.menu[open]");
      if (d) { d.open = false; d.querySelector("summary").focus(); }
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

  /* PDF preview drawn with pdf.js (static/vendor/pdfjs) on canvases: the browser's own viewer is
     missing on Android and is hijacked by download managers (IDM) on desktop. `?raw=1` makes the
     server send the bytes as text/plain without a filename, which download managers ignore.
     Pages are drawn when scrolled near, so long documents stay light. */
  const PDFJS = SPRITE.replace(/icons\.svg$/, "vendor/pdfjs/");
  let pdfjs = null;
  let pdfDoc = null;  // one PDF preview open at a time
  async function pdfFrame(box, src, title) {
    box.replaceChildren(el("div", { class: "pdf-loading", role: "status" },
      el("div", { class: "progress progress-indeterminate" }, el("span")), el("p", { class: "muted", text: t("Loading…") })));
    try {
      pdfjs ||= await import(`${PDFJS}pdf.min.mjs`);
      pdfjs.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.mjs`;
      const r = await fetch(`${src}${src.includes("?") ? "&" : "?"}raw=1`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      pdfDoc?.destroy();
      pdfDoc = await pdfjs.getDocument({
        data: new Uint8Array(await r.arrayBuffer()), isEvalSupported: false,
        cMapUrl: `${PDFJS}cmaps/`, cMapPacked: true, standardFontDataUrl: `${PDFJS}standard_fonts/`,
      }).promise;
      const doc = pdfDoc;
      // dir=ltr: canvas text inherits the page direction, and pdf.js places glyphs one by one
      // assuming LTR. In our RTL page, text in embedded fonts (e.g. Chrome's Save as PDF) came out garbled.
      const pages = el("div", { class: "pdf-pages", dir: "ltr", role: "region", tabindex: "0", "aria-label": title });
      box.replaceChildren(pages);
      const first = (await doc.getPage(1)).getViewport({ scale: 1 });
      const scale = Math.min(2, (pages.clientWidth - 24) / first.width);
      const draw = async (canvas) => {
        const page = await doc.getPage(Number(canvas.dataset.page));
        const vp = page.getViewport({ scale });
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.floor(vp.width * dpr);
        canvas.height = Math.floor(vp.height * dpr);
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp,
          transform: dpr === 1 ? null : [dpr, 0, 0, dpr, 0, 0] }).promise;
      };
      const io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          io.unobserve(e.target);
          draw(e.target).catch(() => e.target.classList.add("pdf-page-error"));
        }
      }, { root: pages, rootMargin: "100% 0px" });
      for (let n = 1; n <= doc.numPages; n++) {
        // Blank canvas at page 1's size until drawn: keeps the scrollbar stable.
        const canvas = el("canvas", { class: "pdf-page", width: Math.floor(first.width * scale),
          height: Math.floor(first.height * scale), "data-page": n, role: "img",
          "aria-label": t("Page {n} of {total}", { n: num(n), total: num(doc.numPages) }) });
        pages.append(canvas);
        io.observe(canvas);
      }
    } catch (e) {
      box.replaceChildren(el("p", { class: "error", role: "alert", text: `${t("Preview failed")}: ${e.message || e}` }));
    }
  }

  return { api, toast, confirm: confirmDialog, prompt: promptDialog, openDialog, formatBytes, formatDate, el, icon, t, num, lang, csrf, refreshUsage, pdfFrame };
})();
