// Keyboard affordances: "/" jumps to the search box, Escape closes whatever is
// open — the search box's focus, the phone drawer, then any expanded job rows.
// Stays out of the way while typing; the confirm <dialog> handles its own Esc.
// Delegated on document so it survives htmx swaps.
function typing(el) {
  return el.closest?.("input, textarea, select, [contenteditable]");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !typing(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const box = document.querySelector('input[name="query"]');
    if (box) {
      e.preventDefault(); // don't type the "/" into the box we just focused
      box.focus();
      box.select();
    }
    return;
  }
  if (e.key !== "Escape") return;
  if (document.getElementById("confirm-dialog")?.open) return; // dialog owns Esc
  if (typing(e.target)) {
    e.target.blur();
    return;
  }
  if (document.body.classList.contains("sidebar-open")) {
    document.body.classList.remove("sidebar-open");
    return;
  }
  for (const d of document.querySelectorAll("#jobs details[open]")) d.open = false;
});
