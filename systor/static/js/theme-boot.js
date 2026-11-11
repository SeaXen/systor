(function () {
  var t = localStorage.getItem("systor_theme") || "warm-dark";
  if (t !== "light" && t !== "classic-dark" && t !== "warm-dark") t = "warm-dark";
  document.documentElement.setAttribute("data-theme", t);
  document.documentElement.style.colorScheme = (t === "light") ? "light" : "dark";
  var tc = document.querySelector('meta[name="theme-color"]');
  if (tc) tc.setAttribute("content", t === "light" ? "#f9f9f7" : (t === "classic-dark" ? "#0f1117" : "#0b0b0b"));
})();
