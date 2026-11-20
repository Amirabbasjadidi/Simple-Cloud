"use strict";

(() => {
  const { api, toast, el, formatBytes, formatDate } = SC;
  const T = (s) => (SC.t ? SC.t(s) : s);
  const listing = document.getElementById("listing");
  const crumbs = document.getElementById("breadcrumbs");
  const search = document.getElementById("search");
  const sortSel = document.getElementById("sort");

  const params = new URLSearchParams(location.search);
  const state = {
    folder: params.get("folder") || null,
    q: params.get("q") || "",
    sort: localGet("sc.sort", "date:desc"),
    view: localGet("sc.view", "grid"),
    data: null,
  };
  search.value = state.q;
  sortSel.value = state.sort;

  function localGet(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
  function localSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }

  /* ---------- icons (inline SVG, currentColor) ---------- */
  const ICONS = {
    folder: "M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2h8.4A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z",
    file: "M6 3h8l4 4v14H6zM14 3v4h4",
    image: "M4 5h16v14H4zM4 16l5-5 4 4 2-2 5 5M15.5 9.5a1 1 0 1 0 0-.01",
    video: "M4 6h12v12H4zM16 10l4-2v8l-4-2",
    audio: "M9 17V5l10-2v12M9 17a3 3 0 1 1-3-3 3 3 0 0 1 3 3zM19 15a3 3 0 1 1-3-3 3 3 0 0 1 3 3z",
    pdf: "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h4",
    archive: "M4 4h16v4H4zM5 8v12h14V8M10 12h4",
    text: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 15h6M9 18h4",
  };
  function icon(name, cls = "icon") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", cls);
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", ICONS[name] || ICONS.file);
    svg.appendChild(p);
    return svg;
  }
  function kind(f) {
    const m = f.mime_type || "";
    if (m.startsWith("image/")) return "image";
    if (m.startsWith("video/")) return "video";
    if (m.startsWith("audio/")) return "audio";
    if (m === "application/pdf") return "pdf";
    if (/zip|rar|7z|tar|gzip|bzip/.test(m)) return "archive";
    if (m.startsWith("text/") || m === "application/json") return "text";
    return "file";
  }
  SC.fileKind = kind;
  SC.icon = icon;

  /* ---------- loading ---------- */
  async function load() {
    listing.setAttribute("aria-busy", "true");
    const [sort, order] = state.sort.split(":");
    const qs = new URLSearchParams({ sort, order });
    if (state.q) qs.set("q", state.q);
    else if (state.folder) qs.set("folder", state.folder);
    try {
      state.data = await api(`/api/files?${qs}`);
      render();
    } catch (e) {
      if (e.status === 404 && state.folder) { navigate(null); return; }
      listing.replaceChildren(el("div", { class: "state state-error", role: "alert" },
        el("p", { text: T("Could not load your files.") + " " + e.message }),
        el("button", { class: "btn", type: "button", onclick: load, text: T("Try again") })));
    } finally {
      listing.setAttribute("aria-busy", "false");
    }
  }

  function navigate(folderId, push = true) {
    state.folder = folderId ? String(folderId) : null;
    state.q = "";
    search.value = "";
    const url = new URL(location.href);
    url.search = state.folder ? `?folder=${state.folder}` : "";
    if (push) history.pushState({ folder: state.folder }, "", url);
    document.getElementById("upload-here").href = "/upload" + (state.folder ? `?folder=${state.folder}` : "");
    load();
  }
  window.addEventListener("popstate", () => {
    navigate(new URLSearchParams(location.search).get("folder"), false);
  });

  /* ---------- rendering ---------- */
  function renderCrumbs() {
    const d = state.data;
    const items = [{ id: null, name: T("My files") }, ...d.breadcrumbs];
    if (d.search) items.push({ id: "search", name: `${T("Search")}: “${state.q}”` });
    crumbs.replaceChildren(...items.map((c, i) => {
      const last = i === items.length - 1;
      return el("li", {}, last
        ? el("span", { "aria-current": "page", text: c.name })
        : el("a", { href: c.id ? `?folder=${c.id}` : "?", onclick: (e) => { e.preventDefault(); navigate(c.id); }, text: c.name }));
    }));
  }

  function render() {
    renderCrumbs();
    const { folders, files } = state.data;
    listing.className = `listing view-${state.view}`;
    document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === state.view)));
    if (!folders.length && !files.length) {
      listing.replaceChildren(el("div", { class: "state state-empty" },
        icon("folder", "icon icon-xl"),
        el("p", { text: state.q ? T("No files match your search.") : T("This folder is empty.") }),
        state.q ? null : el("a", { class: "btn btn-primary", href: document.getElementById("upload-here").href, text: T("Upload files") })));
      return;
    }
    const list = el("ul", { class: "items", role: "list" },
      folders.map(folderItem), files.map(fileItem));
    listing.replaceChildren(list);
  }

  function actionButton(label, onclick, extra = {}) {
    return el("button", { type: "button", class: `btn btn-sm ${extra.class || ""}`, onclick, text: T(label),
      "aria-label": `${T(label)}: ${extra.name || ""}` });
  }

  function folderItem(f) {
    return el("li", { class: "item item-folder" },
      el("button", { type: "button", class: "item-main", onclick: () => navigate(f.id), "aria-label": `${T("Open folder")} ${f.name}` },
        el("span", { class: "thumb" }, icon("folder", "icon icon-lg")),
        el("span", { class: "item-name", text: f.name }),
        el("span", { class: "item-meta", text: T("Folder") })),
      el("div", { class: "item-actions" },
        actionButton("Rename", () => renameFolder(f), { name: f.name }),
        actionButton("Move", () => move("folder", f), { name: f.name }),
        actionButton("Delete", () => deleteFolder(f), { name: f.name, class: "btn-danger-ghost" })));
  }

  function fileItem(f) {
    const k = kind(f);
    const thumb = el("span", { class: "thumb" });
    if (k === "image" && f.mime_type !== "image/svg+xml") {
      thumb.append(el("img", { src: f.preview_url, alt: "", loading: "lazy", decoding: "async" }));
    } else {
      thumb.append(icon(k, "icon icon-lg"));
    }
    const main = el("button", { type: "button", class: "item-main", "aria-label": `${T("Open")} ${f.name}`,
      onclick: () => (SC.preview ? SC.preview(f) : (location.href = f.download_url)) },
      thumb,
      el("span", { class: "item-name", title: f.name, text: f.name }),
      el("span", { class: "item-meta", text: `${formatBytes(f.size)} · ${formatDate(f.created_at)}` }));
    const actions = el("div", { class: "item-actions" },
      el("a", { class: "btn btn-sm", href: f.download_url, download: "", text: T("Download"), "aria-label": `${T("Download")}: ${f.name}` }),
      SC.share ? actionButton("Share", () => SC.share(f), { name: f.name }) : null,
      actionButton("Rename", () => renameFile(f), { name: f.name }),
      actionButton("Move", () => move("file", f), { name: f.name }),
      actionButton("Delete", () => deleteFile(f), { name: f.name, class: "btn-danger-ghost" }));
    return el("li", { class: "item item-file" }, main, actions);
  }

  /* ---------- actions ---------- */
  async function run(promise, okMessage) {
    try {
      await promise;
      if (okMessage) toast(okMessage, "success");
      await load();
      SC.refreshUsage();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  document.getElementById("new-folder").addEventListener("click", async () => {
    const name = await SC.prompt({ title: T("New folder"), label: T("Folder name"), confirmText: T("Create") });
    if (name) run(api("/api/folders", { method: "POST", json: { name, parent_id: state.q ? null : state.folder } }), T("Folder created."));
  });

  async function renameFolder(f) {
    const name = await SC.prompt({ title: T("Rename folder"), label: T("New name"), value: f.name, confirmText: T("Rename") });
    if (name && name !== f.name) run(api(`/api/folders/${f.id}`, { method: "PATCH", json: { name } }), T("Folder renamed."));
  }

  async function renameFile(f) {
    const name = await SC.prompt({ title: T("Rename file"), label: T("New name"), value: f.name, confirmText: T("Rename") });
    if (name && name !== f.name) run(api(`/api/files/${f.id}`, { method: "PATCH", json: { name } }), T("File renamed."));
  }

  async function move(type, item) {
    let folders;
    try { ({ folders } = await api("/api/folders")); } catch (e) { toast(e.message, "error"); return; }
    const byId = new Map(folders.map((f) => [f.id, f]));
    const pathOf = (f) => { const parts = []; for (let x = f; x; x = byId.get(x.parent_id)) parts.unshift(x.name); return parts.join(" / "); };
    const inside = (f, ancestorId) => { for (let x = f; x; x = byId.get(x.parent_id)) if (x.id === ancestorId) return true; return false; };
    const options = [{ value: "root", label: T("My files") }, ...folders
      .filter((f) => type !== "folder" || !inside(f, item.id))
      .map((f) => ({ value: String(f.id), label: pathOf(f) }))
      .sort((a, b) => a.label.localeCompare(b.label))];
    const current = type === "folder" ? item.parent_id : item.folder_id;
    const target = await SC.prompt({ title: `${T("Move")} “${item.name}”`, label: T("Destination folder"),
      options, value: current ? String(current) : "root", confirmText: T("Move") });
    if (target === null) return;
    const value = target === "root" ? null : Number(target);
    const url = type === "folder" ? `/api/folders/${item.id}` : `/api/files/${item.id}`;
    run(api(url, { method: "PATCH", json: type === "folder" ? { parent_id: value } : { folder_id: value } }), T("Moved."));
  }

  async function deleteFile(f) {
    if (await SC.confirm(`“${f.name}” ${T("will be deleted permanently.")}`, { title: T("Delete file?"), confirmText: T("Delete"), danger: true }))
      run(api(`/api/files/${f.id}`, { method: "DELETE" }), T("File deleted."));
  }

  async function deleteFolder(f) {
    if (await SC.confirm(`“${f.name}” ${T("and everything inside it will be deleted permanently.")}`,
      { title: T("Delete folder?"), confirmText: T("Delete"), danger: true }))
      run(api(`/api/folders/${f.id}`, { method: "DELETE" }), T("Folder deleted."));
  }

  /* ---------- toolbar ---------- */
  let searchTimer;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = search.value.trim(); load(); }, 250);
  });
  sortSel.addEventListener("change", () => { state.sort = sortSel.value; localSet("sc.sort", state.sort); load(); });
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view;
    localSet("sc.view", state.view);
    if (state.data) render();
  }));

  SC.reloadFiles = load;
  navigate(state.folder, false);
})();
