const cartCount = document.querySelector('.cart-count');
if (cartCount && 'MutationObserver' in window) {
    const bumpObserver = new MutationObserver(() => {
        cartCount.classList.remove('bump');
        void cartCount.offsetWidth;
        cartCount.classList.add('bump');
    });
    bumpObserver.observe(cartCount, { childList: true, characterData: true, subtree: true });
}

// «Лёгкий режим» для слабых ПК: ~2.5 секунды меряем средний FPS.
// Если он ниже 40 — ставим класс low-fps на <html>, а CSS выключает
// фоновую анимацию и backdrop-filter (см. macos.css, раздел 17).
if ('requestAnimationFrame' in window && !window.__carelineFpsCheck) {
    window.__carelineFpsCheck = true;
    let frames = 0;
    let start = 0;
    const step = (now) => {
        if (!start) start = now;
        frames += 1;
        if (now - start < 2500) {
            requestAnimationFrame(step);
            return;
        }
        const fps = frames / ((now - start) / 1000);
        if (fps < 40) document.documentElement.classList.add('low-fps');
    };
    requestAnimationFrame(step);
}


// =========================================================================
// Глобальный поиск ⌘K / Ctrl+K — оверлей «палитра» создаётся в JS,
// шаблоны страниц не меняются. Результаты тянутся с /api/search/.
// =========================================================================
(function () {
    if (!document.addEventListener) return;

    const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');
    const hint = document.querySelector('.search-open kbd');
    if (hint && isMac) hint.textContent = '⌘K';

    let overlay = null;
    let input = null;
    let results = null;
    let items = [];
    let activeIndex = -1;
    let debounce = null;
    let lastFocus = null;

    function build() {
        if (overlay) return;

        overlay = document.createElement('div');
        overlay.className = 'palette-overlay';
        overlay.hidden = true;

        const box = document.createElement('div');
        box.className = 'palette';

        const field = document.createElement('div');
        field.className = 'palette-field';
        input = document.createElement('input');
        input.type = 'search';
        input.placeholder = 'Врач, аптека, лекарство…';
        input.setAttribute('aria-label', 'Глобальный поиск');
        field.appendChild(input);

        results = document.createElement('div');
        results.className = 'palette-results';

        const hintRow = document.createElement('div');
        hintRow.className = 'palette-hint';
        hintRow.textContent = '↑↓ — выбор · Enter — открыть · Esc — закрыть';

        box.appendChild(field);
        box.appendChild(results);
        box.appendChild(hintRow);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) close();
        });
        input.addEventListener('input', onInput);
        input.addEventListener('keydown', onKeyDown);
    }

    function onInput() {
        clearTimeout(debounce);
        const query = input.value.trim();
        if (query.length < 2) {
            render({ doctors: [], pharmacies: [], medicines: [] }, query);
            return;
        }
        debounce = setTimeout(() => fetchResults(query), 180);
    }

    function fetchResults(query) {
        fetch(`/api/search/?q=${encodeURIComponent(query)}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then((response) => (response.ok ? response.json() : Promise.reject()))
            .then((data) => render(data, query))
            .catch(() => render({ doctors: [], pharmacies: [], medicines: [] }, query));
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function addGroup(title, entries) {
        if (!entries || !entries.length) return;
        const heading = document.createElement('div');
        heading.className = 'palette-group';
        heading.textContent = title;
        results.appendChild(heading);

        entries.forEach((entry) => {
            const link = document.createElement('a');
            link.className = 'palette-item';
            link.href = entry.url;

            const copy = document.createElement('div');
            const strong = document.createElement('strong');
            strong.textContent = entry.title;
            const span = document.createElement('span');
            span.textContent = entry.sub || '';
            copy.appendChild(strong);
            if (entry.sub) copy.appendChild(span);

            link.appendChild(copy);
            results.appendChild(link);
            items.push(link);
        });
    }

    function render(data, query) {
        clear(results);
        items = [];
        activeIndex = -1;

        addGroup('Врачи', data.doctors);
        addGroup('Аптеки', data.pharmacies);
        addGroup('Лекарства', data.medicines);

        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'palette-empty';
            empty.textContent = query && query.length >= 2
                ? 'Ничего не найдено. Уточните запрос.'
                : 'Введите минимум 2 символа: имя врача, аптеку или лекарство.';
            results.appendChild(empty);
        }
    }

    function setActive(nextIndex) {
        if (!items.length) return;
        if (activeIndex >= 0 && items[activeIndex]) items[activeIndex].classList.remove('is-active');
        activeIndex = (nextIndex + items.length) % items.length;
        const active = items[activeIndex];
        active.classList.add('is-active');
        active.scrollIntoView({ block: 'nearest' });
    }

    function onKeyDown(event) {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActive(activeIndex + 1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive(activeIndex - 1);
        } else if (event.key === 'Enter') {
            const target = items[activeIndex] || items[0];
            if (target) {
                event.preventDefault();
                window.location.href = target.href;
            }
        } else if (event.key === 'Escape') {
            close();
        }
    }

    function open() {
        build();
        lastFocus = document.activeElement;
        overlay.hidden = false;
        input.value = '';
        render({ doctors: [], pharmacies: [], medicines: [] }, '');
        document.documentElement.style.overflow = 'hidden';
        input.focus();
    }

    function close() {
        if (!overlay) return;
        overlay.hidden = true;
        document.documentElement.style.overflow = '';
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    document.addEventListener('click', (event) => {
        const trigger = event.target.closest && event.target.closest('.search-open');
        if (trigger) {
            event.preventDefault();
            open();
        }
    });

    document.addEventListener('keydown', (event) => {
        const hotkey = (event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K');
        if (hotkey) {
            event.preventDefault();
            if (overlay && !overlay.hidden) close();
            else open();
        } else if (event.key === 'Escape' && overlay && !overlay.hidden) {
            close();
        }
    });
})();


// =========================================================================
// Скролл-интерфейс: полоса прогресса сверху + кнопка «Наверх».
// Оба элемента создаются JS — шаблоны не трогаем.
// =========================================================================
(function () {
    if (!document.body || !('requestAnimationFrame' in window)) return;

    const bar = document.createElement('div');
    bar.className = 'scroll-progress';
    const fill = document.createElement('span');
    bar.appendChild(fill);
    document.body.appendChild(bar);

    const backToTop = document.createElement('button');
    backToTop.type = 'button';
    backToTop.className = 'back-to-top';
    backToTop.setAttribute('aria-label', 'Наверх');
    backToTop.textContent = '↑';
    document.body.appendChild(backToTop);
    backToTop.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    let ticking = false;
    const update = () => {
        ticking = false;
        const root = document.documentElement;
        const max = root.scrollHeight - window.innerHeight;
        const progress = max > 0 ? Math.min(window.scrollY / max, 1) : 0;
        fill.style.width = `${(progress * 100).toFixed(2)}%`;
        backToTop.classList.toggle('is-visible', window.scrollY > 480);
    };

    window.addEventListener('scroll', () => {
        if (!ticking) {
            ticking = true;
            requestAnimationFrame(update);
        }
    }, { passive: true });
    update();
})();


// =========================================================================
// PWA: регистрируем service worker (network-first — офлайн-фолбэк,
// онлайн всегда отдаёт свежий контент, без «залипания» кэша в дев-режиме).
// =========================================================================
if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
}
