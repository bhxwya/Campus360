/* =========================
   NAVBAR SCROLL STATE
========================= */

const navbar = document.querySelector(".navbar");

if (navbar) {
  const updateNavbarState = () => {
    navbar.classList.toggle("scrolled", window.scrollY > 50);
  };

  updateNavbarState();

  window.addEventListener("scroll", updateNavbarState, { passive: true });
}