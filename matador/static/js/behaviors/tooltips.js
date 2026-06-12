// Tooltips: one fixed element, positioned by JS so it never clips inside a scroll
// container, flips below when there's no room above, and stays within the viewport.
// Tooltip text comes from data-tip; the a11y name comes from the element's
// own (sr-only) content - never aria-label, which would fight the visible label.
const tip = document.createElement("div");
tip.id = "tip";
document.body.appendChild(tip);
let cur = null;

function show(el) {
  const label = el.dataset.tip;
  if (!label) return;
  cur = el;
  tip.textContent = label;
  tip.classList.add("show");
  const r = el.getBoundingClientRect();
  const m = 6;
  let top = r.top - tip.offsetHeight - m;
  if (top < m) top = r.bottom + m; // flip below
  let left = r.left + r.width / 2 - tip.offsetWidth / 2;
  left = Math.max(m, Math.min(left, window.innerWidth - tip.offsetWidth - m)); // clamp
  tip.style.top = top + "px";
  tip.style.left = left + "px";
}
function hide() {
  cur = null;
  tip.classList.remove("show");
}

document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".tip[data-tip]");
  if (el && el !== cur) show(el);
});
document.addEventListener("mouseout", (e) => {
  if (cur && (!e.relatedTarget || !cur.contains(e.relatedTarget))) hide();
});
document.addEventListener("focusin", (e) => {
  const el = e.target.closest(".tip[data-tip]");
  if (el) show(el);
});
document.addEventListener("focusout", hide);
document.addEventListener("click", hide);
window.addEventListener("scroll", hide, true);
