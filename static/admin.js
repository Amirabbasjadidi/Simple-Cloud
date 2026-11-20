"use strict";

(() => {
  const { api, toast, el, formatBytes } = SC;
  const tbody = document.getElementById("users-body");
  const T = (s) => (SC.t ? SC.t(s) : s);

  async function load() {
    try {
      const { users } = await api("/api/admin/users");
      tbody.replaceChildren(...users.map(row));
    } catch (e) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: 6, class: "error", text: e.message })));
    }
  }

  async function patch(u, body, okMessage) {
    try {
      const r = await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: body });
      if (r.temporary_password) {
        await SC.confirm(`${T("Temporary password for")} ${u.username}: ${r.temporary_password}`,
          { title: T("Password reset"), confirmText: T("Done") });
      } else if (okMessage) {
        toast(okMessage, "success");
      }
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function row(u) {
    const quota = el("input", {
      type: "number", min: 0, class: "input-sm", value: u.quota_bytes ? Math.round(u.quota_bytes / 1048576) : "",
      "aria-label": `${T("Quota (MB) for")} ${u.username}`, placeholder: "∞",
    });
    quota.addEventListener("change", () => patch(u, { quota_mb: quota.value || null }, T("Quota updated.")));
    const actions = el("div", { class: "row-actions" },
      el("button", { class: "btn btn-sm", type: "button", disabled: u.is_self,
        onclick: () => patch(u, { disabled: !u.disabled }, u.disabled ? T("User enabled.") : T("User disabled.")),
        text: u.disabled ? T("Enable") : T("Disable") }),
      el("button", { class: "btn btn-sm", type: "button", disabled: u.is_self,
        onclick: () => patch(u, { is_admin: !u.is_admin }, T("Role updated.")),
        text: u.is_admin ? T("Remove admin") : T("Make admin") }),
      el("button", { class: "btn btn-sm", type: "button",
        onclick: async () => {
          if (await SC.confirm(`${T("Reset the password of")} ${u.username}?`, { confirmText: T("Reset") }))
            patch(u, { reset_password: true });
        }, text: T("Reset password") }),
      u.totp_enabled ? el("button", { class: "btn btn-sm", type: "button",
        onclick: async () => {
          if (await SC.confirm(`${T("Turn off two-factor authentication for")} ${u.username}?`, { confirmText: T("Turn off") }))
            patch(u, { reset_2fa: true }, T("Two-factor authentication turned off."));
        }, text: T("Reset 2FA") }) : null,
      el("button", { class: "btn btn-sm btn-danger", type: "button", disabled: u.is_self,
        onclick: () => remove(u), text: T("Delete") }),
    );
    const usage = `${formatBytes(u.used_bytes)} · ${u.file_count} ${T("files")}`;
    return el("tr", {},
      el("th", { scope: "row", text: u.username + (u.is_self ? ` (${T("you")})` : "") }),
      el("td", { text: u.is_admin ? T("Admin") : T("User") }),
      el("td", { text: u.disabled ? T("Disabled") : (u.must_change_password ? T("Must change password") : T("Active")) }),
      el("td", { text: usage }),
      el("td", {}, quota),
      el("td", {}, actions));
  }

  function remove(u) {
    const dlg = document.getElementById("delete-dialog");
    document.getElementById("delete-summary").textContent =
      `${u.username}: ${u.file_count} ${T("files")}, ${formatBytes(u.used_bytes)}. ${T("This cannot be undone.")}`;
    document.getElementById("delete-files").hidden = u.file_count === 0;
    dlg.returnValue = "cancel";
    dlg.showModal();
    dlg.addEventListener("close", async () => {
      if (dlg.returnValue !== "ok") return;
      const files = dlg.querySelector('input[name="files"]:checked').value;
      try {
        await api(`/api/admin/users/${u.id}`, {
          method: "DELETE", json: { files, to: document.getElementById("delete-to").value.trim() },
        });
        toast(T("User deleted."), "success");
        load();
      } catch (e) {
        toast(e.message, "error");
      }
    }, { once: true });
  }

  document.getElementById("create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/users", { method: "POST", json: {
        username: document.getElementById("new-username").value.trim(),
        password: document.getElementById("new-password").value,
        quota_mb: document.getElementById("new-quota").value || null,
        is_admin: document.getElementById("new-admin").checked,
      } });
      toast(T("User created. They must change the password at first login."), "success");
      e.target.reset();
      load();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  load();
})();
