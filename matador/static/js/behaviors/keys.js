// Keyboard affordances: "/" jumps to the search box, Escape closes whatever is
// open - the search box's focus, the phone drawer, then any expanded job rows.
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
  // Gmail-style row cursor: j/k move (real focus, so the focus outline and
  // assistive tech come along), o opens, x selects into the bulk bar. Nothing
  // destructive lives on a key - Gmail pairs those with undo; we don't have one.
  if (["j", "k", "o", "x"].includes(e.key) && !typing(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const rows = [...document.querySelectorAll("#jobs details > summary")];
    if (!rows.length) return;
    const active = document.activeElement;
    const cur = rows.includes(active) ? active : null;
    if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      let i = cur ? rows.indexOf(cur) + (e.key === "j" ? 1 : -1) : 0;
      i = Math.max(0, Math.min(i, rows.length - 1));
      rows[i].focus();
      rows[i].scrollIntoView({ block: "nearest" });
    } else if (cur && e.key === "o") {
      e.preventDefault();
      cur.parentElement.open = !cur.parentElement.open;
    } else if (cur && e.key === "x") {
      e.preventDefault();
      const c = cur.querySelector(".jcheck");
      if (c) {
        c.checked = !c.checked;
        c.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    return;
  }
  if (e.key === "?" && !typing(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    document.getElementById("hotkeys")?.togglePopover();
    return;
  }
  if (e.key !== "Escape") return;
  if (document.getElementById("confirm-dialog")?.open) return; // dialog owns Esc
  if (document.getElementById("hotkeys")?.matches(":popover-open")) return; // popover owns Esc
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
