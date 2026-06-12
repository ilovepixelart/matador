// Job rows are native <details>: a click anywhere in the <summary> toggles the row,
// because toggling is the summary's default action. The per-row action buttons sit
// inside that summary, so clicking the empty padding/gaps around them would toggle the
// row too. preventDefault cancels that default action. (stopPropagation would NOT - the
// toggle is a default action, not a bubbling listener.) The buttons keep working: htmx
// fires its request from its own click handler regardless of preventDefault.
//
// The checkbox/label is deliberately not marked: a <label> forwards its click to the
// checkbox, which consumes the activation, so the row never toggles there anyway - and
// preventDefault would wrongly cancel the checkbox toggle. Delegated on the document so
// it survives htmx swaps without re-binding.
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-no-toggle]")) e.preventDefault();
});
