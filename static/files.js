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
  }

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
    return el("li", { class: "item item-folder" },
      el("button", { type: "button", class: "item-main", onclick: () => navigate(f.id), "aria-label": `${T("Open folder")} ${f.name}` },
        el("span", { class: "thumb" }, icon("folder", "icon-lg")),
        itemText(f.name, T("Folder"))),
      el("div", { class: "item-actions" },
        moreMenu(f.name, [
          ["Rename", "edit", () => renameFolder(f)],
          ["Move", "move", () => move("folder", f)],
          ["Delete", "trash", () => deleteFolder(f), true],
        ])));
  }

  function fileItem(f) {
    const k = kind(f);
    const thumb = el("span", { class: "thumb" });
    if (k === "image" && f.mime_type !== "image/svg+xml") {
      thumb.append(el("img", { src: f.preview_url, alt: "", loading: "lazy", decoding: "async" }));
    } else {
      thumb.append(icon(k, "icon-lg"));
    }
    const main = el("button", { type: "button", class: "item-main", "aria-label": `${T("Open")} ${f.name}`,
      onclick: () => (SC.preview ? SC.preview(f) : (location.href = f.download_url)) },
      thumb, itemText(f.name, `${formatBytes(f.size)} · ${formatDate(f.created_at)}`));
    const actions = el("div", { class: "item-actions" },
      el("a", { class: "btn btn-sm btn-icon btn-ghost", href: f.download_url, download: "", title: T("Download"),
        "aria-label": `${T("Download")}: ${f.name}` }, icon("download")),
      moreMenu(f.name, [
        SC.share ? ["Share", "link", () => SC.share(f)] : null,
        ["Rename", "edit", () => renameFile(f)],
        ["Move", "move", () => move("file", f)],
        ["Delete", "trash", () => deleteFile(f), true],
      ]));
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
    const target = await SC.prompt({ title: `${T("Move")} ${q(item.name)}`, label: T("Destination folder"),
      options, value: current ? String(current) : "root", confirmText: T("Move") });
    if (target === null) return;
    const value = target === "root" ? null : Number(target);
    const url = type === "folder" ? `/api/folders/${item.id}` : `/api/files/${item.id}`;
    run(api(url, { method: "PATCH", json: type === "folder" ? { parent_id: value } : { folder_id: value } }), T("Moved."));
  }

  async function deleteFile(f) {
    if (await SC.confirm(`${q(f.name)} ${T("will be deleted permanently.")}`, { title: T("Delete file?"), confirmText: T("Delete"), danger: true }))
      run(api(`/api/files/${f.id}`, { method: "DELETE" }), T("File deleted."));
  }

  async function deleteFolder(f) {
    if (await SC.confirm(`${q(f.name)} ${T("and everything inside it will be deleted permanently.")}`,
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
