"use strict";

/* Trash: restore, delete for good, empty. Deleting for good can't be undone, so it asks first. */
(() => {
  const { api, toast, el, formatBytes } = SC;
  const T = SC.t;
  const list = document.getElementById("trash-list");
  const emptyBtn = document.getElementById("empty-trash");
  const total = document.getElementById("trash-total");
  const q = (name) => (SC.lang === "fa" ? `«⁨${name}⁩»` : `“⁨${name}⁩”`);
  const DAY = 86400;

  function row(f) {
    const days = Math.max(0, Math.ceil((f.purge_at - Date.now() / 1000) / DAY));
    const meta = [formatBytes(f.size), `${T("From")}: ${f.folder || T("My files")}`,
      T("Deleted for good in {n} days", { n: SC.num(days) })].join(" · ");
    return el("li", { class: "share-row" },
      el("div", { class: "share-info" },
        el("strong", { dir: "auto", text: f.name }),
        el("span", { class: "muted", text: meta })),
      el("div", { class: "row-actions" },
        el("button", { type: "button", class: "btn btn-sm", "aria-label": `${T("Restore")}: ${f.name}`,
          onclick: () => restore(f) }, SC.icon("refresh"), el("span", { text: T("Restore") })),
        el("button", { type: "button", class: "btn btn-sm btn-danger-ghost", "aria-label": `${T("Delete forever")}: ${f.name}`,
          onclick: () => destroy(f) }, el("span", { text: T("Delete forever") }))));
  }

  async function load() {
    try {
      const { files, total_size } = await api("/api/trash");
      emptyBtn.hidden = !files.length;
      total.textContent = files.length ? T("{n} files · {size} still counting toward your storage",
        { n: SC.num(files.length), size: formatBytes(total_size) }) : "";
      list.replaceChildren(...(files.length ? files.map(row)
        : [el("li", { class: "state state-empty" }, SC.icon("trash", "icon-xl"), el("p", { text: T("The trash is empty.") }),
          el("a", { class: "btn", href: "/files" }, SC.icon("folder"), el("span", { text: T("Go to files") })))]));
    } catch (e) {
      list.replaceChildren(el("li", { class: "error", role: "alert", text: e.message }));
    }
  }

  async function restore(f) {
    try {
      const r = await api(`/api/trash/${f.id}/restore`, { method: "POST" });
      toast(`${q(r.name)} ${T("restored.")}`, "success");
      load();
    } catch (e) { toast(e.message, "error"); }
  }

  async function destroy(f) {
    if (!(await SC.confirm(`${q(f.name)} ${T("will be deleted permanently. This cannot be undone.")}`,
      { title: T("Delete forever?"), confirmText: T("Delete forever"), danger: true }))) return;
    try {
      await api(`/api/trash/${f.id}`, { method: "DELETE" });
      toast(T("Deleted permanently."), "success");
      load();
      SC.refreshUsage();
    } catch (e) { toast(e.message, "error"); }
  }

  emptyBtn.addEventListener("click", async () => {
    if (!(await SC.confirm(T("Every file in the trash will be deleted permanently. This cannot be undone."),
      { title: T("Empty the trash?"), confirmText: T("Empty trash"), danger: true }))) return;
    try {
      const r = await api("/api/trash", { method: "DELETE" });
      toast(T("{n} files deleted permanently.", { n: SC.num(r.deleted) }), "success");
      load();
      SC.refreshUsage();
    } catch (e) { toast(e.message, "error"); }
  });

  load();
})();
