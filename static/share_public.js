"use strict";

(() => {
  const meta = document.getElementById("share-meta");
  if (meta) {
    document.getElementById("share-size").textContent = SC.formatBytes(Number(meta.dataset.size));
    const exp = document.getElementById("share-expires");
    if (exp && meta.dataset.expires) {
      const d = new Date(Number(meta.dataset.expires) * 1000);
      exp.dateTime = d.toISOString();
      exp.textContent = d.toLocaleString(document.documentElement.lang || undefined);
    }
  }
  /* Online viewing: the first request to /view uses one download (same as the download button),
     so the player only loads after an explicit click. */
  const viewBtn = document.getElementById("share-view");
  viewBtn?.addEventListener("click", async () => {
    const T = (s) => (SC.t ? SC.t(s) : s);
    const box = document.getElementById("share-viewer");
    const { src, kind, name } = viewBtn.dataset;
    viewBtn.disabled = true;
    viewBtn.setAttribute("aria-expanded", "true");
    box.hidden = false;
    viewBtn.closest(".auth-card").classList.add("is-viewing");
    switch (kind) {
      case "image": box.replaceChildren(SC.el("img", { src, alt: name })); break;
      case "video": box.replaceChildren(SC.el("video", { src, controls: true, preload: "metadata" })); break;
      case "audio": box.replaceChildren(SC.el("audio", { src, controls: true, preload: "metadata" })); break;
      case "pdf": box.replaceChildren(SC.el("iframe", { src, title: name })); break;
      default: {
        box.replaceChildren(SC.el("p", { class: "muted", text: T("Loading…") }));
        try {
          const r = await fetch(src, { headers: { Range: "bytes=0-262143" } });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          box.replaceChildren(SC.el("pre", { class: "preview-text", dir: "auto", tabindex: "0", text: await r.text() }));
        } catch (e) {
          box.replaceChildren(SC.el("p", { class: "error", role: "alert", text: `${T("Preview failed")}: ${e.message}` }));
        }
      }
    }
  });

  const form = document.getElementById("unlock-form");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await SC.api(`/s/${encodeURIComponent(form.dataset.token)}`, {
        method: "POST", json: { password: document.getElementById("share-password").value },
      });
      location.reload();
    } catch (err) {
      SC.toast(err.message, "error");
      document.getElementById("share-password").select();
    }
  });
})();
