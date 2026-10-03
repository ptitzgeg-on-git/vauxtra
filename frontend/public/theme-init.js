// Dark mode before first paint. `theme.tsx` applies `.dark` from a useEffect, which runs after
// the browser has already painted `:root`'s light background: every load flashed white at a
// user who chose dark. This reads the same key (`vauxtra.theme`) and the same
// light/dark/system rule, so the first frame is already the right colour. The provider keeps
// ownership of every runtime change. A separate file because the CSP refuses inline scripts.
(function () {
  try {
    var saved = localStorage.getItem('vauxtra.theme');
    var dark =
      saved === 'dark' ||
      ((!saved || saved === 'system') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', dark);
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  } catch (e) {
    /* storage blocked (private mode): the provider settles it after mount */
  }
})();
