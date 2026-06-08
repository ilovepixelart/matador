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
