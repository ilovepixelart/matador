// Pause the live #jobs refresh while you have a job row open — you're reading a detail,
// so the table shouldn't move/refresh under you. The refresh trigger always fires; this
// just cancels the request while a row is open, so it's a progressive enhancement: if
// this script fails to load, the table still refreshes (it simply won't pause).
//
// Gated on an OPEN <details> only (not selection): a persisted selection can hold a stale
// id and would freeze the table forever; an open row is always visible and self-clears.
document.body.addEventListener("htmx:beforeRequest", (e) => {
  if (e.target.matches("[data-jobs-live]") && document.querySelector("#jobs details[open]")) {
    e.preventDefault(); // skip this refresh; the next event re-fires once the row closes
  }
});

// A jobs fragment may only ever land in #jobs. Rapid queue switching can leave
// htmx listeners glued to recycled DOM nodes (morph keeps the node, htmx keeps
// the init-time verb+path closure); when such a node has lost its hx-target
// attribute the request inherits #queue-panel and a bare jobs fragment eats
// the whole panel. Refuse the request outright when its target isn't #jobs.
document.body.addEventListener("htmx:beforeRequest", (e) => {
  const cfg = e.detail.requestConfig;
  if (cfg?.path?.includes("/jobs?") && cfg.target && cfg.target.id !== "jobs") {
    e.preventDefault();
  }
});

// A live refresh may only update the view it was issued for. A stale response
// (user switched queue/tab while it was in flight) morphing a DIFFERENT view
// into #jobs is how the panel got eaten: idiomorph pairs unrelated nodes
// positionally, htmx's init-time closures stay glued to the recycled nodes,
// and a later SSE tick fires the transplanted listener with no hx-target —
// inheriting #queue-panel. Compare the view identity both fragments already
// carry (data-view="queue:state") and drop mismatched swaps.
document.body.addEventListener("htmx:beforeSwap", (e) => {
  if (!e.detail.target || e.detail.target.id !== "jobs") return;
  const current = e.detail.target.querySelector("[data-view]")?.dataset.view;
  const incoming = /data-view="([^"]+)"/.exec(e.detail.serverResponse || "")?.[1];
  if (current && incoming && current !== incoming) {
    e.detail.shouldSwap = false; // stale response from a view we already left
  }
});
