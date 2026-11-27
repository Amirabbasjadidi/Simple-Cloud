"use strict";

/* Admin dashboard: activity log with filters (user, event, dates, text) and "Load more". */
(() => {
  const { api, toast, el } = SC;
  const T = SC.t;
  const $ = (id) => document.getElementById(id);
  const body = $("activity-body");
  if (!body) return;

  // Readable names; anything new shows its raw name until it gets one here.
  const LABELS = {
    login_ok: "Signed in", login_failed: "Sign-in failed", login_locked: "Sign-in locked", logout: "Signed out",
    password_changed: "Password changed", password_change_failed: "Password change failed",
    totp_enabled: "Two-factor turned on", totp_disabled: "Two-factor turned off", totp_disable_failed: "Two-factor change failed",
    upload: "Uploaded", link_upload: "Uploaded via link", fetch_ok: "Fetched from URL", fetch_failed: "Fetch failed",
    fetch_cancelled: "Fetch cancelled", file_update: "Renamed or moved", delete: "Moved to trash",
    items_delete: "Moved items to trash", folder_create: "Folder created", folder_update: "Folder renamed or moved",
    folder_delete: "Folder deleted", trash_restore: "Restored from trash", trash_delete: "Deleted forever",
    trash_empty: "Trash emptied", zip_download: "Downloaded ZIP",
    share_create: "Share link created", share_revoke: "Share link revoked", share_download: "Share link used",
    share_password_failed: "Share link: wrong password", upload_link_create: "Upload link created",
    upload_link_revoke: "Upload link revoked", upload_link_password_failed: "Upload link: wrong password",
    admin_create_user: "Admin: user created", admin_update_user: "Admin: user changed", admin_delete_user: "Admin: user deleted",
    admin_update_settings: "Admin: settings changed", admin_backup_start: "Admin: backup started",
  };
  const label = (e) => (LABELS[e] ? T(LABELS[e]) : e);
  let next = null;

  function details(d) {
    // The name of the file/folder first, then the rest as key=value.
    const parts = [];
    if (d.name) parts.push(d.name);
    for (const [k, v] of Object.entries(d)) {
      if (k === "name" || v === null || v === "" || v === false) continue;
      parts.push(`${k}=${v}`);
    }
    return parts.join(" · ");
  }

  function row(e) {
    const d = new Date(e.ts * 1000);
    return el("tr", {},
      el("td", { "data-label": T("Time") }, el("time", { datetime: d.toISOString(), text: d.toLocaleString(SC.lang) })),
      el("td", { "data-label": T("User"), dir: "auto", text: e.user || "—" }),
      el("td", { "data-label": T("Action") }, el("span", { class: `badge ${/failed|locked/.test(e.event) ? "badge-danger" : ""}`, text: label(e.event) })),
      el("td", { "data-label": T("Details"), class: "activity-details", dir: "auto", text: details(e.details) }),
      el("td", { "data-label": T("IP"), dir: "ltr", text: e.ip || "" }));
  }

  function query(before) {
    const qs = new URLSearchParams();
    if ($("act-user").value) qs.set("user", $("act-user").value);
    if ($("act-event").value) qs.set("event", $("act-event").value);
    if ($("act-q").value.trim()) qs.set("q", $("act-q").value.trim());
    // Dates are the viewer's local days.
    if ($("act-from").value) qs.set("from", new Date(`${$("act-from").value}T00:00`).getTime() / 1000);
    if ($("act-to").value) qs.set("to", new Date(`${$("act-to").value}T00:00`).getTime() / 1000 + 86400);
    if (before) qs.set("before", before);
    return qs;
  }

  function fillSelect(sel, values, labeller) {
    const keep = sel.value;
    sel.replaceChildren(sel.options[0], ...values.map((v) => el("option", { value: v, text: labeller(v) })));
    sel.value = values.includes(keep) ? keep : "";
  }

  async function load(more = false) {
    const btn = $("act-more");
    btn.disabled = true;
    body.parentElement.setAttribute("aria-busy", "true");
    try {
      const r = await api(`/api/admin/events?${query(more ? next : null)}`);
      if (!more) {
        fillSelect($("act-user"), r.users, (u) => u);
        fillSelect($("act-event"), r.event_types, label);
      }
      const rows = r.events.map(row);
      if (more) body.append(...rows);
      else body.replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 5, class: "muted", text: T("No activity matches these filters.") }))]));
      next = r.next;
      btn.hidden = !next;
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      body.parentElement.setAttribute("aria-busy", "false");
    }
  }

  $("activity-form").addEventListener("submit", (e) => { e.preventDefault(); load(); });
  ["act-user", "act-event", "act-from", "act-to"].forEach((id) => $(id).addEventListener("change", () => load()));
  $("act-reset").addEventListener("click", () => { $("activity-form").reset(); load(); });
  $("act-more").addEventListener("click", () => load(true));
  load();
})();
