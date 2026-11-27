"use strict";

/* Share links (files and folders) and upload links: creation dialogs (file manager) and the
   shares page, which lists both. */
(() => {
  const { api, toast, el } = SC;
  const T = SC.t;

  /* navigator.clipboard exists only on HTTPS (and localhost); on a plain-HTTP LAN install the
     legacy execCommand copy still works. Last resort: show the link to copy by hand. */
  function legacyCopy(text) {
    const ta = el("textarea", { readonly: true, class: "sr-only" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { /* unsupported */ }
    ta.remove();
    return ok;
  }

  async function copy(text) {
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { /* denied */ }
    }
    if (!ok) ok = legacyCopy(text);
    if (ok) toast(T("Link copied."), "success", 2500);
    else await SC.prompt({ title: T("Share link"), label: T("Copy this link"), value: text, confirmText: T("Done") });
  }

  function describe(s) {
    const parts = [];
    if (s.kind === "folder") parts.push(T("Folder"));
    if (s.expired) parts.push(T("Expired"));
    else if (s.expires_at) parts.push(`${T("Expires")} ${new Date(s.expires_at * 1000).toLocaleString(document.documentElement.lang || undefined)}`);
    else parts.push(T("Never expires"));
    if (s.has_password) parts.push(T("Password"));
    if (s.login_required) parts.push(T("Signed-in users only"));
    parts.push(s.max_downloads ? `${SC.num(s.download_count)}/${SC.num(s.max_downloads)} ${T("downloads")}` : `${SC.num(s.download_count)} ${T("downloads")}`);
    return parts.join(" · ");
  }

  function describeLink(l) {
    const parts = [`${T("Into")}: ${l.folder || T("My files")}`];
    if (l.expired) parts.push(T("Expired"));
    else if (l.expires_at) parts.push(`${T("Expires")} ${new Date(l.expires_at * 1000).toLocaleString(document.documentElement.lang || undefined)}`);
    if (l.has_password) parts.push(T("Password"));
    if (l.login_required) parts.push(T("Signed-in users only"));
    parts.push(l.max_files ? `${SC.num(l.file_count)}/${SC.num(l.max_files)} ${T("files")}` : `${SC.num(l.file_count)} ${T("files")}`);
    return parts.join(" · ");
  }

  function linkRow(l, onChange) {
    return el("li", { class: `share-row ${l.expired ? "is-expired" : ""}` },
      el("div", { class: "share-info" },
        el("strong", { dir: "auto", text: l.folder || T("My files") }),
        el("span", { class: "muted", text: describeLink(l) })),
      el("div", { class: "row-actions" },
        el("button", { type: "button", class: "btn btn-sm", disabled: l.expired, onclick: () => copy(l.url) },
          SC.icon("copy"), el("span", { text: T("Copy link") })),
        el("button", { type: "button", class: "btn btn-sm btn-danger-ghost", text: T("Revoke"),
          onclick: async () => {
            if (!(await SC.confirm(T("Nobody will be able to upload through this link any more. Files already uploaded stay."),
              { title: T("Revoke link?"), confirmText: T("Revoke"), danger: true }))) return;
            try { await api(`/api/upload-links/${l.id}`, { method: "DELETE" }); toast(T("Link revoked."), "success"); onChange(); }
            catch (e) { toast(e.message, "error"); }
          } })));
  }

  function shareRow(s, onChange, showFile = false) {
    return el("li", { class: `share-row ${s.expired ? "is-expired" : ""}` },
      el("div", { class: "share-info" },
        showFile ? el("strong", { dir: "auto", text: s.file_name }) : null,
        el("span", { class: "muted", text: describe(s) })),
      el("div", { class: "row-actions" },
        el("button", { type: "button", class: "btn btn-sm", disabled: s.expired,
          "aria-label": `${T("Copy link")}: ${s.file_name}`, onclick: () => copy(s.url) }, SC.icon("copy"), el("span", { text: T("Copy link") })),
        el("button", { type: "button", class: "btn btn-sm btn-danger-ghost", text: T("Revoke"),
          "aria-label": `${T("Revoke")}: ${s.file_name}`,
          onclick: async () => {
            if (!(await SC.confirm(T("People with this link will no longer be able to download the file."),
              { title: T("Revoke link?"), confirmText: T("Revoke"), danger: true }))) return;
            try { await api(`/api/shares/${s.id}`, { method: "DELETE" }); toast(T("Link revoked."), "success"); onChange(); }
            catch (e) { toast(e.message, "error"); }
          } })));
  }

  /* File manager dialog */
  const dlg = document.getElementById("share-dialog");
  if (dlg) {
    /* type: "file" or "folder" (a folder share lists its files and offers the whole folder as a ZIP). */
    SC.share = async (file, type = "file") => {
      document.getElementById("share-file").textContent = file.name;
      document.getElementById("share-folder-hint").hidden = type !== "folder";
      const existing = document.getElementById("share-existing");
      const loadExisting = async () => {
        try {
          const { shares } = await api(`/api/shares?${type}_id=${file.id}`);
          existing.replaceChildren(...(shares.length ? [el("h3", { text: T("Existing links") }),
            el("ul", { class: "share-list", role: "list" }, shares.map((s) => shareRow(s, loadExisting)))] : []));
        } catch (e) { existing.replaceChildren(el("p", { class: "error", text: e.message })); }
      };
      loadExisting();
      // Stay open after creating a link so it can be copied; the dialog closes with Close/Escape.
      while (await SC.openDialog(dlg)) {
        try {
          const { share } = await api(`/api/${type === "folder" ? "folders" : "files"}/${file.id}/shares`, { method: "POST", json: {
            expires_hours: document.getElementById("share-expiry").value || null,
            password: document.getElementById("share-pass").value,
            max_downloads: document.getElementById("share-max").value || null,
            login_required: document.getElementById("share-login").checked,
          } });
          document.getElementById("share-pass").value = "";
          await copy(share.url);
          await loadExisting();
        } catch (e) {
          toast(e.message, "error");
        }
      }
    };
  }

  /* Upload link dialog (file manager): folder = a folder row, or null for "My files" */
  const linkDlg = document.getElementById("upload-link-dialog");
  if (linkDlg) {
    SC.uploadLink = async (folder) => {
      document.getElementById("upload-link-folder").textContent = folder ? folder.name : T("My files");
      const existing = document.getElementById("upload-link-existing");
      const folderId = folder ? folder.id : "root";
      const loadExisting = async () => {
        try {
          const { links } = await api(`/api/upload-links?folder_id=${folderId}`);
          existing.replaceChildren(...(links.length ? [el("h3", { text: T("Existing links") }),
            el("ul", { class: "share-list", role: "list" }, links.map((l) => linkRow(l, loadExisting)))] : []));
        } catch (e) { existing.replaceChildren(el("p", { class: "error", text: e.message })); }
      };
      loadExisting();
      while (await SC.openDialog(linkDlg)) {
        try {
          const { link } = await api("/api/upload-links", { method: "POST", json: {
            folder_id: folder ? folder.id : null,
            expires_hours: document.getElementById("link-expiry").value || null,
            password: document.getElementById("link-pass").value,
            max_files: document.getElementById("link-max").value || null,
            login_required: document.getElementById("link-login").checked,
          } });
          document.getElementById("link-pass").value = "";
          await copy(link.url);
          await loadExisting();
        } catch (e) {
          toast(e.message, "error");
        }
      }
    };
  }

  /* Shares page: upload links */
  const linkList = document.getElementById("upload-links-list");
  if (linkList) {
    const loadLinks = async () => {
      try {
        const { links } = await api("/api/upload-links");
        linkList.replaceChildren(...(links.length ? links.map((l) => linkRow(l, loadLinks))
          : [el("li", { class: "muted", text: T("No upload links yet. Use “Upload link” in the file manager.") })]));
      } catch (e) {
        linkList.replaceChildren(el("li", { class: "error", role: "alert", text: e.message }));
      }
    };
    loadLinks();
  }

  /* Shares page */
  const list = document.getElementById("shares-list");
  if (list) {
    const load = async () => {
      try {
        const { shares } = await api("/api/shares");
        list.replaceChildren(...(shares.length ? shares.map((s) => shareRow(s, load, true))
          : [el("li", { class: "state state-empty" }, SC.icon("link", "icon-xl"), el("p", { text: T("You have not shared any files yet. Use “Share” in the file manager.") }),
            el("a", { class: "btn btn-primary", href: "/files" }, SC.icon("folder"), el("span", { text: T("Go to files") })))]));
      } catch (e) {
        list.replaceChildren(el("li", { class: "error", role: "alert", text: e.message }));
      }
    };
    load();
  }
})();
