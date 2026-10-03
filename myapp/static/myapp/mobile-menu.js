const menuToggle = document.querySelector('.mobile-menu-toggle');
const primaryNavigation = document.querySelector('#primary-navigation');

if (menuToggle && primaryNavigation) {
    const closeMenu = () => {
        menuToggle.setAttribute('aria-expanded', 'false');
        menuToggle.setAttribute('aria-label', 'Открыть меню');
        primaryNavigation.classList.remove('is-open');
    };

    menuToggle.addEventListener('click', () => {
        const isOpen = menuToggle.getAttribute('aria-expanded') === 'true';
        menuToggle.setAttribute('aria-expanded', String(!isOpen));
        menuToggle.setAttribute('aria-label', isOpen ? 'Открыть меню' : 'Закрыть меню');
        primaryNavigation.classList.toggle('is-open', !isOpen);
    });

    primaryNavigation.addEventListener('click', (event) => {
        if (event.target.closest('a')) closeMenu();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeMenu();
    });
}