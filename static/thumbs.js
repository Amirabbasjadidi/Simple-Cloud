"use strict";

/* Thumbnails for PDFs (page 1) and videos (a frame near the start), drawn here once and stored on
   the server (PUT /files/<id>/thumb), so the next visit just loads an image. One at a time, in the
   background. If a file can't be drawn, an empty upload records that, so nobody tries again. */
(() => {
  const WIDTH = 320;
  const MAX_PDF = 50 * 1024 * 1024;  // bigger PDFs: not worth downloading whole for a thumbnail
  const queue = [];
  let busy = false;

  const toJpeg = (canvas) => new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.75));

  async function pdfThumb(job) {
    if (job.size > MAX_PDF) return null;
    const doc = await SC.openPdf(job.preview);
    try {
      const page = await doc.getPage(1);
      const base = page.getViewport({ scale: 1 });
      const vp = page.getViewport({ scale: WIDTH / base.width });
      // Not attached to the page: canvas text stays LTR, which pdf.js needs (see SC.pdfFrame).
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvasContext: ctx, viewport: vp }).promise;
      return toJpeg(canvas);
    } finally {
      doc.destroy();
    }
  }

  function videoThumb(job) {
    return new Promise((resolve, reject) => {
      const v = document.createElement("video");
      const timer = setTimeout(() => { cleanup(); reject(new Error("timeout")); }, 20000);
      const cleanup = () => { clearTimeout(timer); v.removeAttribute("src"); v.load(); };
      v.muted = true;
      v.preload = "metadata";
      v.addEventListener("loadedmetadata", () => { v.currentTime = Math.min(1, (v.duration || 0) / 10); });
      v.addEventListener("seeked", async () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = WIDTH;
          canvas.height = Math.round(WIDTH * (v.videoHeight / v.videoWidth || 9 / 16));
          canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
          resolve(await toJpeg(canvas));
        } catch (e) { reject(e); } finally { cleanup(); }
      }, { once: true });
      v.addEventListener("error", () => { cleanup(); reject(new Error("cannot decode")); }, { once: true });
      v.src = job.preview;
    });
  }

  async function run(job, holder) {
    let blob = null;
    try {
      blob = await (job.kind === "pdf" ? pdfThumb(job) : videoThumb(job));
    } catch (_) { /* unsupported codec, broken PDF...: recorded as "no thumbnail" below */ }
    const r = await fetch(job.thumb, { method: "PUT", body: blob || new Blob([]),
      headers: { "X-CSRFToken": SC.csrf, "Content-Type": "image/jpeg" } });
    if (r.ok && blob && holder.isConnected) {
      const img = SC.el("img", { src: `${job.thumb}?v=${Date.now()}`, alt: "", decoding: "async" });
      holder.replaceChildren(img);
    }
  }

  async function pump() {
    if (busy) return;
    busy = true;
    while (queue.length) {
      const holder = queue.shift();
      if (!holder.isConnected) continue;  // the user moved to another folder
      try { await run(JSON.parse(holder.dataset.makeThumb), holder); } catch (_) { /* next one */ }
    }
    busy = false;
  }

  SC.queueThumbs = (root) => {
    queue.length = 0;  // a new listing replaces the old queue
    queue.push(...root.querySelectorAll("[data-make-thumb]"));
    pump();
  };
})();
