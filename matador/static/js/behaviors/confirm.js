// Custom confirmation dialog for any element with hx-confirm (delete/clean/etc.).
// We intercept htmx's confirm step, show our own <dialog>, and only issue the
// request if the user confirms - so destructive actions never hit window.confirm.
//
// Everything is looked up fresh and delegated on document, never captured at load.
// A full-page history restore (Back/Forward re-fetches the URL and replaces the
// whole <body>) swaps in a NEW dialog node; a captured reference would go stale
// and showModal() would throw "element is not in a Document", so the modal would
// silently stop opening. Delegation survives that.
let pending = null;

document.addEventListener("htmx:confirm", (e) => {
  if (!e.detail.question) return; // no hx-confirm → let the request through
  const dlg = document.getElementById("confirm-dialog");
  if (!dlg) return;
  e.preventDefault(); // suppress the native confirm()
  document.getElementById("confirm-text").textContent = e.detail.question;
  pending = e.detail;
  dlg.showModal();
});

document.addEventListener("click", (e) => {
  if (e.target.closest("#confirm-ok")) {
    document.getElementById("confirm-dialog")?.close();
    if (pending) {
      const p = pending;
      pending = null;
      p.issueRequest(true);
    }
  } else if (e.target.closest("#confirm-cancel") || e.target.matches?.("#confirm-dialog")) {
    // cancel button, or a click on the dialog's own backdrop (target is the dialog)
    document.getElementById("confirm-dialog")?.close();
  }
});

// Esc / programmatic close drops any pending request so a later confirm can't
// fire it. `close` doesn't bubble, so listen in the capture phase.
document.addEventListener(
  "close",
  (e) => {
    if (e.target?.id === "confirm-dialog") pending = null;
  },
  true,
);
