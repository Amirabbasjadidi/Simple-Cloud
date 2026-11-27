"use strict";

(() => {
  const { api, toast, el, formatBytes, formatDate } = SC;
  const T = SC.t;
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
    selected: new Map(),  // "file:12" / "folder:3" -> item
  };
  search.value = state.q;
  sortSel.value = state.sort;

  function localGet(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
  function localSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }

  const icon = SC.icon;
  // Quote a user-supplied name; bidi isolates keep Latin names readable inside Persian sentences.
  const q = (name) => (SC.lang === "fa" ? `«⁨${name}⁩»` : `“⁨${name}⁩”`);
  SC.quote = q;

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

  /* ---------- loading ---------- */
  async function load() {
    listing.setAttribute("aria-busy", "true");
    const [sort, order] = state.sort.split(":");
    const qs = new URLSearchParams({ sort, order });
    if (state.q) qs.set("q", state.q);
    else if (state.folder) qs.set("folder", state.folder);
    try {
      state.data = await api(`/api/files?${qs}`);
      state.selected.clear();
      render();
    } catch (e) {
      if (e.status === 404 && state.folder) { navigate(null); return; }
      listing.replaceChildren(el("div", { class: "state state-error", role: "alert" },
        icon("alert", "icon-xl"),
        el("p", { text: T("Could not load your files.") + " " + e.message }),
        el("button", { class: "btn", type: "button", onclick: load }, icon("refresh"), el("span", { text: T("Try again") }))));
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
    if (d.search) items.push({ id: "search", name: `${T("Search")}: ${q(state.q)}` });
    crumbs.replaceChildren(...items.map((c, i) => {
      const last = i === items.length - 1;
      return el("li", {}, last
        ? el("span", { "aria-current": "page", text: c.name })
        : el("a", { href: c.id ? `?folder=${c.id}` : "?", dir: "auto", onclick: (e) => { e.preventDefault(); navigate(c.id); }, text: c.name }));
    }));
  }

  function render() {
    renderCrumbs();
    const { folders, files } = state.data;
    listing.className = `listing view-${state.view}`;
    document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === state.view)));
    if (!folders.length && !files.length) {
      listing.replaceChildren(el("div", { class: "state state-empty" },
        icon(state.q ? "search" : "folder", "icon-xl"),
        el("p", { text: state.q ? T("No files match your search.") : T("This folder is empty.") }),
        state.q ? null : el("a", { class: "btn btn-primary", href: document.getElementById("upload-here").href },
          icon("upload"), el("span", { text: T("Upload files") }))));
      return;
    }
    const list = el("ul", { class: "items", role: "list" },
      folders.map(folderItem), files.map(fileItem));
    listing.replaceChildren(list);
    updateSelection();
    if (SC.queueThumbs) SC.queueThumbs(list);
  }

  /* ---------- selection ---------- */
  const bar = document.getElementById("selection-bar");
  function checkbox(type, item, li) {
    const key = `${type}:${item.id}`;
    const box = el("input", { type: "checkbox", class: "item-check", "aria-label": `${T("Select")} ${item.name}` });
    box.checked = state.selected.has(key);
    li.classList.toggle("is-selected", box.checked);
    box.addEventListener("change", () => {
      if (box.checked) state.selected.set(key, { type, item }); else state.selected.delete(key);
      li.classList.toggle("is-selected", box.checked);
      updateSelection();
    });
    return box;
  }
  function updateSelection() {
    const n = state.selected.size;
    bar.hidden = !n;
    listing.classList.toggle("selecting", n > 0);
    document.getElementById("selection-count").textContent = T("{n} selected", { n: SC.num(n) });
  }
  const picked = (type) => [...state.selected.values()].filter((s) => s.type === type).map((s) => s.item);
  document.getElementById("sel-clear").addEventListener("click", () => { state.selected.clear(); render(); });
  document.getElementById("sel-download").addEventListener("click", () => {
    const files = picked("file");
    const folders = picked("folder");
    if (files.length === 1 && !folders.length) { location.href = files[0].download_url; return; }
    const qs = new URLSearchParams();
    if (files.length) qs.set("files", files.map((f) => f.id).join(","));
    if (folders.length) qs.set("folders", folders.map((f) => f.id).join(","));
    location.href = `/files/zip?${qs}`;
  });
  document.getElementById("sel-delete").addEventListener("click", async () => {
    const files = picked("file");
    const folders = picked("folder");
    if (folders.length && !(await SC.confirm(T("The selected folders are removed. All files inside them go to the trash."),
      { title: T("Delete selected items?"), confirmText: T("Delete"), danger: true }))) return;
    try {
      const r = await api("/api/items/delete", { method: "POST", json: { files: files.map((f) => f.id), folders: folders.map((f) => f.id) } });
      toast(T("{n} files moved to the trash.", { n: SC.num(r.deleted_files) }), "success", 8000,
        !folders.length ? { label: T("Undo"), onClick: () => restore(files) } : null);
      await load();
      SC.refreshUsage();
    } catch (e) { toast(e.message, "error"); }
  });

  /* Overflow menu (<details>) so every row keeps one compact "More" button on small screens. */
  function moreMenu(name, entries) {
    return el("details", { class: "menu" },
      el("summary", { class: "btn btn-sm btn-icon btn-ghost", "aria-label": `${T("More actions")}: ${name}`, title: T("More actions") }, icon("more")),
      el("ul", { class: "menu-list", role: "list" }, entries.filter(Boolean).map(([label, ic, onclick, danger]) =>
        el("li", {}, el("button", { type: "button", class: `menu-item ${danger ? "danger" : ""}`, onclick }, icon(ic), el("span", { text: T(label) }))))));
  }

  function itemText(name, meta) {
    return el("span", { class: "item-text" },
      el("span", { class: "item-name", dir: "auto", title: name, text: name }),
      el("span", { class: "item-meta", text: meta }));
  }

  function folderItem(f) {
    const li = el("li", { class: "item item-folder" });
    li.append(checkbox("folder", f, li),
      el("button", { type: "button", class: "item-main", onclick: () => navigate(f.id), "aria-label": `${T("Open folder")} ${f.name}` },
        el("span", { class: "thumb" }, icon("folder", "icon-lg")),
        itemText(f.name, T("Folder"))),
      el("div", { class: "item-actions" },
        moreMenu(f.name, [
          ["Download as ZIP", "archive", () => { location.href = `/files/zip?folders=${f.id}`; }],
          SC.share ? ["Share", "link", () => SC.share(f, "folder")] : null,
          SC.uploadLink ? ["Upload link", "upload", () => SC.uploadLink(f)] : null,
          ["Rename", "edit", () => renameFolder(f)],
          ["Move", "move", () => move("folder", f)],
          ["Delete", "trash", () => deleteFolder(f), true],
        ])));
    return li;
  }

  function fileItem(f) {
    const k = kind(f);
    const thumb = el("span", { class: "thumb" });
    if (k === "image" && f.mime_type !== "image/svg+xml") {
      thumb.append(el("img", { src: f.preview_url, alt: "", loading: "lazy", decoding: "async" }));
    } else if (f.thumb === "ready" || (f.thumb === "missing" && f.preview_kind === "audio")) {
      // audio: the server reads the embedded cover on first request; no cover -> back to the icon
      const img = el("img", { src: f.thumb_url, alt: "", loading: "lazy", decoding: "async",
        class: f.preview_kind === "audio" ? "thumb-cover" : "" });
      img.addEventListener("error", () => img.replaceWith(icon(k, "icon-lg")));
      thumb.append(img);
    } else {
      thumb.append(icon(k, "icon-lg"));
      if (f.thumb === "missing") thumb.dataset.makeThumb = JSON.stringify({ id: f.id, kind: f.preview_kind,
        size: f.size, preview: f.preview_url, thumb: f.thumb_url });
    }
    const by = f.uploaded_by ? ` · ${T("Uploaded by")} ${f.uploaded_by}` : (f.via_link ? ` · ${T("Via upload link")}` : "");
    const main = el("button", { type: "button", class: "item-main", "aria-label": `${T("Open")} ${f.name}`,
      onclick: () => (SC.preview ? SC.preview(f) : (location.href = f.download_url)) },
      thumb, itemText(f.name, `${formatBytes(f.size)} · ${formatDate(f.created_at)}${by}`));
    const actions = el("div", { class: "item-actions" },
      el("a", { class: "btn btn-sm btn-icon btn-ghost", href: f.download_url, download: "", title: T("Download"),
        "aria-label": `${T("Download")}: ${f.name}` }, icon("download")),
      moreMenu(f.name, [
        SC.share ? ["Share", "link", () => SC.share(f)] : null,
        ["Rename", "edit", () => renameFile(f)],
        ["Move", "move", () => move("file", f)],
        ["Delete", "trash", () => deleteFile(f), true],
      ]));
    const li = el("li", { class: "item item-file" });
    li.append(checkbox("file", f, li), main, actions);
    return li;
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
    const target = await SC.prompt({ title: `${T("Move")} ${q(item.name)}`, label: T("Destination folder"),
      options, value: current ? String(current) : "root", confirmText: T("Move") });
    if (target === null) return;
    const value = target === "root" ? null : Number(target);
    const url = type === "folder" ? `/api/folders/${item.id}` : `/api/files/${item.id}`;
    run(api(url, { method: "PATCH", json: type === "folder" ? { parent_id: value } : { folder_id: value } }), T("Moved."));
  }

  /* A file goes to the trash: no confirmation, an Undo in the toast instead. */
  async function deleteFile(f) {
    try {
      await api(`/api/files/${f.id}`, { method: "DELETE" });
      toast(`${q(f.name)} ${T("moved to the trash.")}`, "success", 8000, { label: T("Undo"), onClick: () => restore([f]) });
      await load();
    } catch (e) { toast(e.message, "error"); }
  }

  async function restore(files) {
    try {
      for (const f of files) await api(`/api/trash/${f.id}/restore`, { method: "POST" });
      toast(T("Restored."), "success");
      await load();
    } catch (e) { toast(e.message, "error"); }
  }

  /* Folders are removed (their files go to the trash), so this one still asks. */
  async function deleteFolder(f) {
    if (await SC.confirm(`${q(f.name)} ${T("is removed. Its files go to the trash, where you can restore them.")}`,
      { title: T("Delete folder?"), confirmText: T("Delete"), danger: true }))
      run(api(`/api/folders/${f.id}`, { method: "DELETE" }), T("Folder deleted. Its files are in the trash."));
  }

  document.getElementById("folder-upload-link")?.addEventListener("click", () =>
    SC.uploadLink(state.data?.folder || null));

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
