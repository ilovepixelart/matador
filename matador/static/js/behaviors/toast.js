// Auto-dismiss error toasts (response-targets swaps them into #toast on a failed
// action). They're click-to-dismiss too; this just removes them after a few seconds
// if left untouched.
const zone = document.getElementById("toast");
if (zone) {
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) setTimeout(() => node.remove(), 5000);
      }
    }
  }).observe(zone, { childList: true });
}
