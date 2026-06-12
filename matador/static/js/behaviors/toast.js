// Error toasts (response-targets swaps them into #toast on a failed action). They
// auto-dismiss after a few seconds, and are click-anywhere-to-dismiss - both handled
// here so the toast markup stays a plain, listener-free fragment.
const zone = document.getElementById("toast");
if (zone) {
  zone.addEventListener("click", (e) => {
    e.target.closest(".toast")?.remove();
  });
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) setTimeout(() => node.remove(), 5000);
      }
    }
  }).observe(zone, { childList: true });
}
