// Custom confirmation dialog for any element with hx-confirm (delete/clean/etc.).
// We intercept htmx's confirm step, show our own <dialog>, and only issue the
// request if the user confirms — so destructive actions never hit window.confirm.
const dlg = document.getElementById("confirm-dialog");
const text = document.getElementById("confirm-text");
let pending = null;

document.addEventListener("htmx:confirm", (e) => {
  if (!e.detail.question) return; // no hx-confirm → let the request through
  e.preventDefault(); // suppress the native confirm()
  text.textContent = e.detail.question;
  pending = e.detail;
  dlg.showModal();
});
document.getElementById("confirm-ok").addEventListener("click", () => {
  dlg.close();
  if (pending) {
    const p = pending;
    pending = null;
    p.issueRequest(true);
  }
});
document.getElementById("confirm-cancel").addEventListener("click", () => dlg.close());
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); }); // backdrop
dlg.addEventListener("close", () => { pending = null; }); // Esc/cancel
