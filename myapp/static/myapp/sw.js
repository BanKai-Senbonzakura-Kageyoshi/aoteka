/* Careline service worker: network-first.
   Онлайн — всегда свежий ответ (в дев-режиме ничего не «залипает»),
   офлайн — отдаём то, что успели закэшировать (фолбэк — главная). */
const CACHE = 'careline-v5';

self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET' || !/^https?:$/.test(new URL(request.url).protocol)) return;

    event.respondWith(
        fetch(request)
            .then((response) => {
                if (response && response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
                }
                return response;
            })
            .catch(() =>
                caches.match(request).then((cached) => cached || caches.match('/'))
            )
    );
});
