/* Loaded in <head> before the stylesheet applies, so the saved theme is set without a flash.
   No choice saved: follow the operating system. */
(function () {
  var t = null;
  try { t = localStorage.getItem("sc.theme"); } catch (e) { /* storage blocked */ }
  if (t !== "dark" && t !== "light") t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
})();
