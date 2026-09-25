// Classic (non-module) script loaded in <head> so a saved theme applies before first paint.
try {
  const saved = localStorage.getItem("ledger-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch {
  // Storage unavailable (private window, blocked site data): follow the system theme.
}
