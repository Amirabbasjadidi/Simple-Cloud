"use strict";

/* Multi-file, drag-and-drop, chunked + resumable upload with per-file progress.
   Used on the upload page (into a chosen folder) and on upload-link pages, where the dropzone's
   data-api points at the link's endpoints and the link decides the folder. */
(() => {
  const { api, toast, el, formatBytes } = SC;
  const T = (s) => (SC.t ? SC.t(s) : s);
  const PARALLEL = 2;
  const RETRIES = 5;
  const queueEl = document.getElementById("queue");
  const dropzone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const API = dropzone.dataset.api || "/api/uploads";
  const folderSel = document.getElementById("upload-folder");
  let folder = new URLSearchParams(location.search).get("folder") || "";
  const queue = [];
  let active = 0;

  /* Folder picker: every folder by its full path; the choice is kept in the address (?folder=). */
  if (folderSel) {
    api("/api/folders").then(({ folders }) => {
      const byId = new Map(folders.map((f) => [f.id, f]));
      const path = (f) => { const p = []; for (let x = f; x; x = byId.get(x.parent_id)) p.unshift(x.name); return p.join(" / "); };
      folderSel.append(...folders.map((f) => ({ value: String(f.id), label: path(f) }))
        .sort((a, b) => a.label.localeCompare(b.label))
        .map((o) => el("option", { value: o.value, dir: "auto", text: o.label })));
      if (byId.has(Number(folder))) folderSel.value = folder; else folder = "";
    }).catch((e) => toast(e.message, "error"));
    folderSel.addEventListener("change", () => {
      folder = folderSel.value;
      const url = new URL(location.href);
      if (folder) url.searchParams.set("folder", folder); else url.searchParams.delete("folder");
      history.replaceState(null, "", url);
    });
  }

  const keyFor = (f, target) => `sc.upload:${API}:${target || ""}:${f.name}:${f.size}:${f.lastModified}`;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (_) { /* private mode */ } },
  };

  function addFiles(files) {
    document.getElementById("queue-empty").hidden = true;
    for (const file of files) {
      const bar = el("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100,
        "aria-valuenow": 0, "aria-label": `${T("Upload progress")}: ${file.name}` }, el("span"));
      const status = el("span", { class: "queue-status muted", text: T("Waiting…") });
      const cancelBtn = el("button", { type: "button", class: "btn btn-sm", text: T("Cancel"),
        "aria-label": `${T("Cancel")}: ${file.name}` });
      // The folder is fixed when the file is added: changing the picker later doesn't move it.
      const item = { file, bar, status, cancelBtn, cancelled: false, xhr: null, uploadId: null, folder };
      cancelBtn.addEventListener("click", () => cancel(item));
      queueEl.append(el("li", { class: "queue-item" },
        el("div", { class: "queue-head" },
          SC.icon(SC.fileKind ? SC.fileKind({ mime_type: file.type }) : "file"),
          el("span", { class: "queue-name", dir: "auto", title: file.name, text: file.name }),
          el("span", { class: "muted", text: formatBytes(file.size) }), cancelBtn),
        bar, status));
      queue.push(item);
    }
    pump();
  }

  function pump() {
    while (active < PARALLEL && queue.length) {
      const item = queue.shift();
      if (item.cancelled) continue;
      active++;
      upload(item).finally(() => { active--; pump(); });
    }
  }

  function setProgress(item, done) {
    const pct = item.file.size ? Math.floor((done / item.file.size) * 100) : 100;
    item.bar.querySelector("span").style.width = `${pct}%`;
    item.bar.setAttribute("aria-valuenow", String(pct));
    item.status.textContent = `${SC.num(pct)}% · ${formatBytes(done)} / ${formatBytes(item.file.size)}`;
  }

  function sendChunk(item, offset, blob) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      item.xhr = xhr;
      xhr.open("PUT", `${API}/${encodeURIComponent(item.uploadId)}?offset=${offset}`);
      xhr.setRequestHeader("X-CSRFToken", SC.csrf);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => setProgress(item, offset + e.loaded);
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
        if (xhr.status === 200) resolve(data);
        else {
          const err = new Error(data.error || `HTTP ${xhr.status}`);
          err.status = xhr.status;
          err.data = data;
          reject(err);
        }
      };
      xhr.onerror = () => reject(Object.assign(new Error(T("Network error")), { retry: true }));
      xhr.onabort = () => reject(Object.assign(new Error(T("Cancelled")), { cancelled: true }));
      xhr.send(blob);
    });
  }

  async function upload(item) {
    const { file } = item;
    const key = keyFor(file, item.folder);
    try {
      let received = 0;
      let chunkSize = 8 * 1024 * 1024;
      const saved = store.get(key);
      if (saved) {
        try {
          const s = await api(`${API}/${encodeURIComponent(saved)}`);
          if (s.size === file.size) { item.uploadId = saved; received = s.received; }
        } catch (_) { store.del(key); }
      }
      if (!item.uploadId) {
        const r = await api(API, { method: "POST", json: { name: file.name, size: file.size, folder_id: item.folder || null } });
        item.uploadId = r.upload_id;
        chunkSize = r.chunk_size;
        if (r.file) { done(item, key, r.file); return; }
        store.set(key, item.uploadId);
      } else if (received) {
        toast(`${T("Resuming")} ${file.name}`, "info", 3000);
      }
      setProgress(item, received);
      let failures = 0;
      while (received < file.size) {
        if (item.cancelled) return;
        const end = Math.min(received + chunkSize, file.size);
        try {
          const r = await sendChunk(item, received, file.slice(received, end));
          received = r.received;
          failures = 0;
          if (r.file) { done(item, key, r.file); return; }
        } catch (e) {
          if (e.cancelled || item.cancelled) return;
          if (e.status === 409 && e.data && typeof e.data.received === "number") { received = e.data.received; continue; }
          if ((e.retry || e.status >= 500) && e.status !== 507 && ++failures <= RETRIES) {
            item.status.textContent = `${T("Connection problem, retrying…")} (${failures}/${RETRIES})`;
            await new Promise((r) => setTimeout(r, 1000 * 2 ** failures));
            continue;
          }
          throw e;
        }
      }
    } catch (e) {
      if (e.status === 404) store.del(key);
      item.status.textContent = `${T("Failed")}: ${e.message}`;
      item.status.className = "queue-status error";
      item.bar.classList.add("progress-error");
      item.cancelBtn.hidden = true;
      toast(`${file.name}: ${e.message}`, "error");
    }
  }

  function done(item, key, f) {
    store.del(key);
    setProgress(item, item.file.size);
    item.bar.classList.add("progress-done");
    item.status.textContent = `${T("Uploaded")}${f.name !== item.file.name ? ` ${T("as")} ${SC.quote ? SC.quote(f.name) : f.name}` : ""}`;
    item.status.className = "queue-status";
    item.cancelBtn.hidden = true;
    SC.refreshUsage();
  }

  async function cancel(item) {
    item.cancelled = true;
    item.xhr?.abort();
    item.cancelBtn.hidden = true;
    item.status.textContent = T("Cancelled");
    store.del(keyFor(item.file, item.folder));
    if (item.uploadId) {
      try { await api(`${API}/${encodeURIComponent(item.uploadId)}`, { method: "DELETE" }); } catch (_) { /* gone */ }
    }
  }

  input.addEventListener("change", () => { addFiles([...input.files]); input.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, () => dropzone.classList.remove("dragover")));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = [...e.dataTransfer.files];
    if (files.length) addFiles(files);
  });
  // Dropping outside the zone must not navigate away from the page.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  window.addEventListener("beforeunload", (e) => { if (active) e.preventDefault(); });
})();
