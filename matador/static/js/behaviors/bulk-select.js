// Multi-select bulk actions. Selected job ids live in a Set that survives htmx swaps
// (so the selection persists as you paginate); it resets when the queue/state view
// changes. The delete submits the WHOLE set via hx-vals="js:{ids: Matador.bulk.ids()}".
const selected = new Set();
let view = null;

window.Matador = window.Matador || {};
window.Matador.bulk = {
  ids: () => [...selected].join(","),
  clear: () => {
    selected.clear();
    sync();
  },
};

function sync() {
  document.querySelectorAll(".jcheck").forEach((c) => {
    c.checked = selected.has(c.value);
  });
  const n = selected.size;
  const bar = document.getElementById("bulk-bar");
  if (bar) bar.style.display = n ? "flex" : "none";
  const cnt = document.getElementById("bulk-count");
  if (cnt) cnt.textContent = n;
  const del = document.getElementById("bulk-delete");
  if (del) del.setAttribute("hx-confirm", `Delete ${n} job${n === 1 ? "" : "s"}?`);
  const all = document.getElementById("select-all");
  if (all) {
    const boxes = document.querySelectorAll(".jcheck");
    const onPage = [...boxes].filter((c) => c.checked).length;
    all.checked = boxes.length > 0 && onPage === boxes.length;
    all.indeterminate = !all.checked && n > 0;
  }
}

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.classList && t.classList.contains("jcheck")) {
    if (t.checked) selected.add(t.value);
    else selected.delete(t.value);
    sync();
  } else if (t.id === "select-all") {
    document.querySelectorAll(".jcheck").forEach((c) => {
      c.checked = t.checked;
      if (t.checked) selected.add(c.value);
      else selected.delete(c.value);
    });
    sync();
  }
});

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-js-bulk-clear]")) window.Matador.bulk.clear();
});

// After any htmx swap: drop the selection if the queue/state view changed (it
// belonged to the old view), then re-apply to whatever page is now shown.
function onSwap() {
  const el = document.querySelector("[data-view]");
  const v = el ? el.getAttribute("data-view") : view;
  if (v !== view) {
    selected.clear();
    view = v;
  }
  sync();
}
// afterSettle fires once the DOM (incl. OOB swaps) is final, so the bar state set
// here sticks; rAF guards against any late style application.
document.body.addEventListener("htmx:afterSettle", () => requestAnimationFrame(onSwap));
// Clear once a bulk-remove has been issued — centralised here so it doesn't depend
// on the triggering button surviving its own swap.
document.body.addEventListener("htmx:afterRequest", (e) => {
  const p =
    (e.detail.pathInfo && e.detail.pathInfo.requestPath) ||
    (e.detail.requestConfig && e.detail.requestConfig.path) ||
    "";
  if (p.includes("/jobs/bulk-remove")) selected.clear();
});

const start = document.querySelector("[data-view]");
view = start ? start.getAttribute("data-view") : null;
