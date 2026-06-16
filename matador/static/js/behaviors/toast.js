// Error toasts (response-targets swaps them into #toast on a failed action). They
// auto-dismiss after a few seconds, and are click-anywhere-to-dismiss - both handled
// here so the toast markup stays a plain, listener-free fragment.
//
// Delegated on document and observed on document.body (not the #toast node), so it
// keeps working after a full-page history restore replaces #toast with a fresh node.
document.addEventListener("click", (e) => {
  e.target.closest("#toast .toast")?.remove();
});

new MutationObserver((mutations) => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType === 1 && node.matches?.(".toast")) {
        setTimeout(() => node.remove(), 5000);
      }
    }
  }
}).observe(document.body, { childList: true, subtree: true });
