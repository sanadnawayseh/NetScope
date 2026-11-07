// Navigation remains ordinary anchors, including when JavaScript is unavailable.
(() => {
  const links = [...document.querySelectorAll('.sidebar .nav')];
  function activate(hash) {
    for (const link of links) {
      const current = link.getAttribute('href') === hash;
      link.classList.toggle('active', current);
      if (current) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }
  function followHash() { activate(location.hash || '#overview'); }
  for (const link of links) link.addEventListener('click', () => {
    const section = document.querySelector(link.getAttribute('href'));
    if (section) {
      section.setAttribute('tabindex', '-1');
      section.focus({preventScroll:true});
    }
    activate(link.getAttribute('href'));
  });
  window.addEventListener('hashchange', followHash);
  followHash();
})();
