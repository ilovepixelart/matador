// Keyboard affordances: "/" jumps to the search box, Escape closes whatever is
// open - the search box's focus, the phone drawer, then any expanded job rows.
// Stays out of the way while typing; the confirm <dialog> handles its own Esc.
// Delegated on document so it survives htmx swaps.
function typing(el) {
  return el.closest?.("input, textarea, select, [contenteditable]");
}

function chorded(e) {
  return e.metaKey || e.ctrlKey || e.altKey;
}

function focusSearch(e) {
  const box = document.querySelector('input[name="query"]');
  if (!box) return;
  e.preventDefault(); // don't type the "/" into the box we just focused
  box.focus();
  box.select();
}

// Where j/k lands: from nowhere it enters at the top; otherwise one step,
// clamped to the list.
function nextRowIndex(rows, cur, key) {
  if (!cur) return 0;
  const step = key === "j" ? 1 : -1;
  return Math.max(0, Math.min(rows.indexOf(cur) + step, rows.length - 1));
}

function toggleRowCheck(summary) {
  const check = summary.querySelector(".jcheck");
  if (!check) return;
  check.checked = !check.checked;
  check.dispatchEvent(new Event("change", { bubbles: true }));
}

// Gmail-style row cursor: j/k move (real focus, so the focus outline and
// assistive tech come along), o opens, x selects into the bulk bar. Nothing
// destructive lives on a key - Gmail pairs those with undo; we don't have one.
function rowCursor(e) {
  const rows = [...document.querySelectorAll("#jobs details > summary")];
  if (!rows.length) return;
  const active = document.activeElement;
  const cur = rows.includes(active) ? active : null;
  if (e.key === "j" || e.key === "k") {
    e.preventDefault();
    const i = nextRowIndex(rows, cur, e.key);
    rows[i].focus();
    rows[i].scrollIntoView({ block: "nearest" });
  } else if (cur && e.key === "o") {
    e.preventDefault();
    cur.parentElement.open = !cur.parentElement.open;
  } else if (cur && e.key === "x") {
    e.preventDefault();
    toggleRowCheck(cur);
  }
}

function onEscape(e) {
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
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    onEscape(e);
    return;
  }
  if (typing(e.target) || chorded(e)) return;
  if (e.key === "/") {
    focusSearch(e);
    return;
  }
  if (e.key === "?") {
    e.preventDefault();
    document.getElementById("hotkeys")?.togglePopover();
    return;
  }
  if (["j", "k", "o", "x"].includes(e.key)) rowCursor(e);
});
