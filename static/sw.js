/* Gmail OTP Dashboard - service worker
   - app shell offline-e kaj kore (login page + shell)
   - /api/* KOKHONO cache hoy na (OTP private data)
   - Web Push: tab/app bondho thakleo notification dekhay */
const VERSION = 'v6';
const SHELL_CACHE = 'otp-shell-' + VERSION;
const ASSET_CACHE = 'otp-assets-' + VERSION;
const SHELL = ['/', '/manifest.webmanifest', '/static/icons/icon-192.png', '/static/icons/icon-512.png',
               '/static/icons/favicon-32.png', '/static/icons/apple-touch-icon.png',
               '/static/jsQR.min.js', '/static/brands.js',
               '/static/app.css', '/static/app.js'];
const CDN_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com'];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            .then((cache) => Promise.all(SHELL.map((u) => cache.add(u).catch(() => {}))))
        // skipWaiting ekhane NEI: notun SW "waiting"-e thake, user banner-e "Update" click korle tobe chalu hoy
    );
});

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== ASSET_CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

function staleWhileRevalidate(request, cacheName) {
    return caches.open(cacheName).then((cache) =>
        cache.match(request).then((hit) => {
            const network = fetch(request)
                .then((res) => {
                    if (res && (res.ok || res.type === 'opaque')) cache.put(request, res.clone());
                    return res;
                })
                .catch(() => hit);
            return hit || network;
        })
    );
}

/* Notun version pele page-ke janai */
async function notifyUpdated() {
    const list = await self.clients.matchAll({ type: 'window' });
    list.forEach((c) => c.postMessage({ type: 'app-updated' }));
}

/* NETWORK-FIRST (timeout shoho):
   - network theke ms-er moddhe uttor ashle SEITAI dekhay (mane shobshomoy notun version) + cache update kore
   - net slow/offline hole cache theke dekhay, ar pichone notun ta ene rekhe page-ke "app-updated" janay
   Age ekhane stale-while-revalidate chilo -- tai prottekbar ekta version pichone thakto. */
async function networkFirst(event, key, ms, isPage) {
    const req = event.request;
    const cache = await caches.open(SHELL_CACHE);
    let timedOut = false;

    const network = (isPage ? fetch(req) : fetch(req.url, { cache: 'no-cache' })).then(async (res) => {
        if (res && res.ok && !res.redirected) {
            let changed = false;
            if (timedOut && isPage) {
                const old = await cache.match(key);
                if (old) {
                    try { changed = (await old.clone().text()) !== (await res.clone().text()); } catch (e) {}
                }
            }
            await cache.put(key, res.clone());
            if (changed) notifyUpdated();
        }
        return res;
    });
    event.waitUntil(network.catch(() => {}));            // SW beche thakbe jotokkhon cache update na hoy

    const timer = new Promise((resolve) => setTimeout(() => resolve(null), ms));
    const first = await Promise.race([network.catch(() => null), timer]);
    if (first) return first;

    timedOut = true;
    const hit = await cache.match(key);
    if (hit) return hit;
    return network.catch(() => Response.error());
}

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    if (url.origin === self.location.origin) {
        if (url.pathname.startsWith('/api/')) return;              // private data: kokhono cache na
        if (req.mode === 'navigate') {
            event.respondWith(url.pathname === '/' ? networkFirst(event, '/', 4000, true)
                                                   : fetch(req).catch(() => caches.match('/').then((h) => h || Response.error())));
            return;
        }
        if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
            event.respondWith(networkFirst(event, req.url, 3000, false));
        }
        return;
    }
    if (CDN_HOSTS.includes(url.hostname)) event.respondWith(staleWhileRevalidate(req, ASSET_CACHE));
});

/* ---------------- Web Push ---------------- */
self.addEventListener('push', (event) => {
    let d = {};
    try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : '' }; }
    const title = d.title || 'New verification code';
    const options = {
        body: d.body || 'Open the dashboard to see it.',
        tag: d.tag || 'otp',               // ek account-er notun code purono-ta-r jaygay boshe
        renotify: true,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/badge-96.png',
        data: { url: d.url || '/' },
        timestamp: d.ts || Date.now()
    };
    // Safari/iOS: prottek push-e notification dekhate-i hoy
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const target = (event.notification.data && event.notification.data.url) || '/';
    event.waitUntil((async () => {
        const list = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const c of list) {
            if ('focus' in c) {
                await c.focus();
                c.postMessage({ type: 'otp-click' });
                return;
            }
        }
        if (self.clients.openWindow) await self.clients.openWindow(target);
    })());
});
