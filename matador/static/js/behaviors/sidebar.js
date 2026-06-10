// Off-canvas sidebar for small screens. The open state lives on <body> (not on
// #sidebar) so a live OOB refresh of the sidebar can't snap an open drawer shut.
// Delegated on document so it survives htmx swaps.
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-js-sidebar-toggle]")) {
    document.body.classList.toggle("sidebar-open");
    return;
  }
  if (e.target.closest("#sidebar-backdrop")) {
    document.body.classList.remove("sidebar-open");
    return;
  }
  // Picking a queue (or Workers) navigated — the drawer's job is done.
  if (e.target.closest("#sidebar a")) {
    document.body.classList.remove("sidebar-open");
  }
});
