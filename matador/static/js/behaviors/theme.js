// Dark/light theme. The saved theme is applied inline in <head> before paint (to
// avoid a flash); this only handles the toggle button + persistence. Delegated on
// document so it works regardless of swaps.
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-js-theme-toggle]")) return;
  const dark = document.documentElement.classList.toggle("dark");
  localStorage.matadorTheme = dark ? "dark" : "light";
});
