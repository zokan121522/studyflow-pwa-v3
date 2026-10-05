(() => {
  const headers = document.querySelectorAll('.accordion-header');
  headers.forEach(h => {
    h.addEventListener('click', () => {
      const content = h.nextElementSibling;
      if (content) content.classList.toggle('open');
      const icon = h.querySelector('.accordion-icon');
      if (icon) icon.textContent = content?.classList.contains('open') ? '−' : '+';
    });
  });
})();
