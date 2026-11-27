"use strict";

/* In-browser preview: image, video, audio, PDF and text (first 256 KB). */
(() => {
  const TEXT_LIMIT = 256 * 1024;
  const T = (s) => (SC.t ? SC.t(s) : s);

  async function textPreview(f, body) {
    body.replaceChildren(SC.el("p", { class: "muted", text: T("Loading…") }));
    try {
      const r = await fetch(f.preview_url, { headers: { Range: `bytes=0-${TEXT_LIMIT - 1}` } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const text = await r.text();
      const total = Number((r.headers.get("Content-Range") || "").split("/")[1]) || f.size;
      // replaceChildren() would print a null argument as the text "null".
      body.replaceChildren(SC.el("pre", { class: "preview-text", dir: "auto", tabindex: "0", text }),
        ...(total > TEXT_LIMIT ? [SC.el("p", { class: "muted", text: T("Showing the first 256 KB. Download the file to see all of it.") })] : []));
    } catch (e) {
      body.replaceChildren(SC.el("p", { class: "error", role: "alert", text: `${T("Preview failed")}: ${e.message}` }));
    }
  }

  SC.preview = (f) => {
    if (!f.preview_kind) { window.location.href = f.download_url; return; }
    const dlg = document.getElementById("preview-dialog");
    const body = document.getElementById("preview-body");
    document.getElementById("preview-title").textContent = f.name;
    document.getElementById("preview-download").href = f.download_url;
    const src = f.preview_url;
    switch (f.preview_kind) {
      case "image": body.replaceChildren(SC.el("img", { src, alt: f.name })); break;
      case "video": body.replaceChildren(SC.el("video", { src, controls: true, preload: "metadata" })); break;
      case "audio": body.replaceChildren(SC.el("audio", { src, controls: true, preload: "metadata" })); break;
      case "pdf": SC.pdfFrame(body, src, f.name); break;
      default: textPreview(f, body);
    }
    // Emptying the body stops media playback when the dialog closes.
    SC.openDialog(dlg).then(() => body.replaceChildren());
  };
})();
