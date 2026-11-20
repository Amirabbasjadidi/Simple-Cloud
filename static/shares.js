"use strict";

/* Share links: creation dialog (file manager) and the shares list page. */
(() => {
  const { api, toast, el } = SC;
  const T = (s) => (SC.t ? SC.t(s) : s);

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
    if (s.expired) parts.push(T("Expired"));
    else if (s.expires_at) parts.push(`${T("Expires")} ${new Date(s.expires_at * 1000).toLocaleString(document.documentElement.lang || undefined)}`);
    else parts.push(T("Never expires"));
    if (s.has_password) parts.push(T("Password"));
    parts.push(s.max_downloads ? `${SC.num(s.download_count)}/${SC.num(s.max_downloads)} ${T("downloads")}` : `${SC.num(s.download_count)} ${T("downloads")}`);
    return parts.join(" · ");
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
    SC.share = async (file) => {
      document.getElementById("share-file").textContent = file.name;
      const existing = document.getElementById("share-existing");
      const loadExisting = async () => {
        try {
          const { shares } = await api(`/api/shares?file_id=${file.id}`);
          existing.replaceChildren(...(shares.length ? [el("h3", { text: T("Existing links") }),
            el("ul", { class: "share-list", role: "list" }, shares.map((s) => shareRow(s, loadExisting)))] : []));
        } catch (e) { existing.replaceChildren(el("p", { class: "error", text: e.message })); }
      };
      loadExisting();
      // Stay open after creating a link so it can be copied; the dialog closes with Close/Escape.
      while (await SC.openDialog(dlg)) {
        try {
          const { share } = await api(`/api/files/${file.id}/shares`, { method: "POST", json: {
            expires_hours: document.getElementById("share-expiry").value || null,
            password: document.getElementById("share-pass").value,
            max_downloads: document.getElementById("share-max").value || null,
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
