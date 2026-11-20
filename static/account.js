"use strict";

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const current = document.getElementById("current-password");
  const next = document.getElementById("new-password");
  const repeat = document.getElementById("confirm-password");
  if (next.value !== repeat.value) {
    SC.toast("The new passwords do not match.", "error");
    repeat.focus();
    return;
  }
  try {
    await SC.api("/api/account/password", {
      method: "POST", json: { current_password: current.value, new_password: next.value },
    });
    SC.toast("Password changed.", "success");
    e.target.reset();
    if (document.querySelector(".alert-warning")) setTimeout(() => (window.location.href = "/files"), 800);
  } catch (err) {
    SC.toast(err.message, "error");
  }
});
