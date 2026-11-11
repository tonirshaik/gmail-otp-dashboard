/* Midnight skin: wires the bottom bar / sidebar to the existing dashboard controls.
   No app logic lives here -- it only clicks the buttons app.js already handles. */
(function () {
    'use strict';
    var nav = document.getElementById('appNav');
    if (!nav) return;
    var items = nav.querySelectorAll('.nav-item');
    var $ = function (id) { return document.getElementById(id); };
    var open = function (id) { var m = $(id); return !!m && !m.classList.contains('hidden'); };

    function setActive(name) {
        items.forEach(function (b) {
            var on = b.getAttribute('data-nav') === name;
            b.classList.toggle('on', on);
            if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
        });
    }
    function current() {
        var pop = $('settingsPopup');
        if (pop && pop.classList.contains('active')) return 'settings';
        if (open('smsModal')) return 'sms';
        if (open('tfaModal')) return 'tfa';
        if (open('accountsModal')) return 'accounts';
        return 'home';
    }
    function sync() { setActive(current()); }

    function closeAll() {
        var pop = $('settingsPopup');
        if (pop && pop.classList.contains('active') && $('settingsToggle')) $('settingsToggle').click();
        ['mailModal:mailCloseBtn', 'tfaModal:tfaCloseBtn', 'accountsModal:accountsCloseBtn', 'backupModal:backupCloseBtn', 'smsModal:smsCloseBtn']
            .forEach(function (p) {
                var parts = p.split(':');
                if (open(parts[0]) && $(parts[1])) $(parts[1]).click();
            });
    }

    nav.addEventListener('click', function (e) {
        var b = e.target.closest('.nav-item');
        if (!b) return;
        var name = b.getAttribute('data-nav');
        e.stopPropagation(); // app.js closes the settings popup on any outside click; keep it open when opened from the nav
        if (name === 'home') {
            closeAll();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        } else if (name === 'accounts') {
            closeAll();
            if ($('accountCountBar')) $('accountCountBar').click();
        } else if (name === 'tfa') {
            closeAll();
            // open the 2FA services (authenticator) list directly, not through the accounts popup
            if (typeof openTfaModal === 'function') openTfaModal('services', '', b);
            else if ($('tfaServiceBtn')) $('tfaServiceBtn').click();
        } else if (name === 'sms') {
            closeAll();
            openSmsPage();
        } else if (name === 'settings') {
            var wasOpen = current() === 'settings';
            closeAll();
            window.scrollTo({ top: 0 });
            if (!wasOpen && $('settingsToggle')) $('settingsToggle').click();
        }
        setTimeout(sync, 260);
    });

    var mo = new MutationObserver(sync);
    ['accountsModal', 'tfaModal', 'settingsPopup', 'mailModal', 'smsModal'].forEach(function (id) {
        var el = $(id);
        if (el) mo.observe(el, { attributes: true, attributeFilter: ['class'] });
    });
    /* ---- Accounts / 2FA are pages: a click on the empty page area must not close them ---- */
    ['accountsModal', 'tfaModal'].forEach(function (id) {
        var m = $(id);
        if (m) m.addEventListener('click', function (e) { if (e.target === m) e.stopImmediatePropagation(); }, true);
    });

    /* ---- Add account (Accounts page): settings password first, then the form ---- */
    var addBtn = $('addAccountBtn'), panel = $('addAccountPanel'), gate = $('addGateForm'),
        wrap = $('addFormWrap'), msg = $('addMsg');
    function say(text, bad) {
        if (!msg) return;
        msg.textContent = text || '';
        msg.style.display = text ? 'block' : 'none';
        msg.style.color = bad ? 'var(--accent-red)' : 'var(--accent-green)';
    }
    function panelOpen() { return !!panel && !panel.classList.contains('hidden'); }
    function closePanel() {
        if (!panel) return;
        panel.classList.add('hidden');
        if (addBtn) addBtn.setAttribute('aria-expanded', 'false');
        if (gate) { gate.classList.remove('hidden'); gate.reset(); }
        if ($('addAccountForm')) $('addAccountForm').reset();
        if (wrap) wrap.classList.add('hidden');
        say('');
    }
    function openPanel() {
        closePanel();
        panel.classList.remove('hidden');
        addBtn.setAttribute('aria-expanded', 'true');
        if ($('addGatePw')) $('addGatePw').focus();
    }
    if (addBtn && panel) {
        addBtn.addEventListener('click', function () { if (panelOpen()) closePanel(); else openPanel(); });
        if ($('addCancelBtn')) $('addCancelBtn').addEventListener('click', closePanel);
        gate.addEventListener('submit', function (e) {
            e.preventDefault();
            var pw = $('addGatePw').value.trim();
            if (!pw) return;
            say('');
            fetch('/api/settings-unlock', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw })
            }).then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
            }).then(function (x) {
                if (x.ok) {
                    gate.classList.add('hidden'); gate.reset();
                    wrap.classList.remove('hidden');
                    if ($('accEmail')) $('accEmail').focus();
                } else {
                    say((x.j && x.j.error) || 'Incorrect password. Please try again.', true);
                }
            }).catch(function () { say('Server Error!', true); });
        });
        // app.js fires this after a successful save
        document.addEventListener('midnight:account-added', function () {
            closePanel();
            if (typeof showToast === 'function') showToast('Account added');
        });
        // app.js reports add errors through #statusMsg (hidden behind the page) -> show them in the panel
        var sm = $('statusMsg');
        if (sm) new MutationObserver(function () {
            if (panelOpen() && !wrap.classList.contains('hidden') && sm.textContent) say(sm.textContent, true);
        }).observe(sm, { childList: true, characterData: true, subtree: true });
        // leaving the Accounts page closes the panel
        var am = $('accountsModal');
        if (am) new MutationObserver(function () { if (am.classList.contains('hidden')) closePanel(); })
            .observe(am, { attributes: true, attributeFilter: ['class'] });
    }

    /* ---- Settings > Preferences: test notification (was a Quick find command) ---- */
    var testBtn = $('testAlertBtn');
    if (testBtn) testBtn.addEventListener('click', function () {
        if (typeof sendTestAlert === 'function') sendTestAlert();
    });

    /* ---- 2FA Services opens instantly: the list is preloaded after login and kept in memory.
       Forget it when the dashboard locks / logs out. ---- */
    var dash = $('dashboardSection');
    if (dash) new MutationObserver(function () {
        if (dash.classList.contains('hidden') && typeof svcList !== 'undefined') { svcList = []; svcLoaded = false; }
    }).observe(dash, { attributes: true, attributeFilter: ['class'] });

    /* ---- Every card always shows its Gmail address (also before a code is found, on errors, and in 2FA mode) ---- */
    if (typeof window.cardHTML === 'function' && !window.cardHTML.__emailRow) {
        var origCard = window.cardHTML;
        var wrappedCard = function (item) {
            var html = origCard.apply(this, arguments);
            if (!item || html.indexOf('class="svc-row"') !== -1) return html;
            var addr = String(item.email || '');
            var row = '<div class="svc-row">' + avatarHTML({ code: '', email: addr }) +
                '<div class="svc-text"><div class="svc-name svc-name-email" title="' + escAttr(addr) + '">' + escAttr(addr) + '</div></div></div>';
            return html.replace('<div class="otp-time">', function () { return row + '<div class="otp-time">'; });
        };
        wrappedCard.__emailRow = true;
        window.cardHTML = wrappedCard;
    }

    /* ---- SMS OTP page (codes forwarded from an Android phone) ---- */
    var smsModal = $('smsModal'), smsScroll = $('smsScroll'), smsSetup = $('smsSetup');
    var smsItems = [], smsLoaded = false, smsKnown = null, smsReveal = {}, smsPoll = null, smsTick = null, smsBusy = false, smsSig = '';

    function smsCardHTML(it, now) {
        var live = it.endsAt > now;
        var masked = !(smsReveal[it.id] && now - smsReveal[it.id] < 15000);
        var sub = (it.sender && it.sender !== it.service ? it.sender + ' \u00b7 ' : '') + mailAgo(it.ts);
        var box = live
            ? '<div class="otp-code-box has-cells sms-code' + (masked ? ' masked' : '') + '" role="button" tabindex="0" data-id="' + escAttr(it.id) +
              '" title="Tap to reveal and copy" aria-label="' + (masked ? 'Code hidden. Press to reveal and copy' : 'Code ' + String(it.code).split('').join(' ') + '. Press to copy') +
              '">' + codeCellsHTML(it.code, masked) + '</div>'
            : '<div class="otp-code-box expired-box"><i class="fa-regular fa-clock"></i> Code expired</div>';
        var note = live && it.confidence === 'low' ? '<div class="sms-note">Please check the SMS to be sure this is the right code.</div>' : '';
        var timer = live ? '<div class="otp-expiry"><span class="tbar" aria-hidden="true"><i class="tbar-fill"></i></span><span class="otp-expiry-lbl">Expires in <span class="otp-expiry-time"></span></span></div>' : '';
        return '<div class="sms-card' + (live ? '' : ' is-expired') + '" data-id="' + escAttr(it.id) + '"' +
            (live ? ' data-ends="' + it.endsAt + '" data-ttl="' + (it.ttl * 1000) + '"' : '') + '>' +
            '<div class="svc-row">' + avatarHTML({ code: 'x', service: it.service, sender: it.service, email: it.sender }) +
            '<div class="svc-text"><div class="svc-name">' + escAttr(it.service) + '</div><div class="svc-sub">' + escAttr(sub) + '</div></div>' +
            '<button type="button" class="sms-del" data-id="' + escAttr(it.id) + '" title="Delete" aria-label="Delete this SMS code"><i class="fa-regular fa-trash-can"></i></button></div>' +
            box + note + timer + '</div>';
    }

    function smsRender() {
        if (!smsScroll) return;
        if (!smsLoaded) { smsScroll.innerHTML = '<div class="mail-state">Loading...</div>'; return; }
        if (!smsItems.length) {
            smsScroll.innerHTML = '<div class="tfa-empty"><i class="fa-solid fa-message"></i>No SMS codes yet. Create the secret link in Settings (SMS OTP setup), then forward SMS from your phone.</div>';
            return;
        }
        var now = Date.now();
        smsScroll.innerHTML = '<div class="sms-list">' + smsItems.map(function (it) { return smsCardHTML(it, now); }).join('') + '</div>';
        smsTickUpdate();
    }

    function smsTickUpdate() {
        if (!smsScroll) return;
        var now = Date.now(), expired = false;
        smsScroll.querySelectorAll('.sms-card[data-ends]').forEach(function (c) {
            var left = Number(c.dataset.ends) - now;
            if (left <= 0) { expired = true; return; }
            var t = c.querySelector('.otp-expiry-time');
            if (t) t.textContent = fmtLeft(Math.ceil(left / 1000));
            var ttl = Number(c.dataset.ttl) || 120000;
            c.classList.toggle('expiring', left <= Math.min(20000, ttl * 0.25));
            setTbar(c, left, ttl, true);
        });
        if (expired) smsRender();
    }

    function smsTickAll() {
        var now = Date.now(), re = false;
        Object.keys(smsReveal).forEach(function (id) {
            if (now - smsReveal[id] >= 15000) { delete smsReveal[id]; re = true; }
        });
        if (re) smsRender(); else smsTickUpdate();
    }

    function smsAlert(it) {
        try { if (typeof beep === 'function') beep(); } catch (e) {}
        try { if (navigator.vibrate) navigator.vibrate([120, 80, 120]); } catch (e) {}
        if (typeof autoCopyOn !== 'undefined' && autoCopyOn && document.visibilityState === 'visible' && typeof copyQuiet === 'function') {
            copyQuiet(it.code).then(function (ok) { showToast(ok ? 'New SMS code copied: ' + it.service : 'New SMS code: ' + it.service); });
        } else {
            showToast('New SMS code: ' + it.service);
        }
    }

    function smsLoad() {
        if (smsBusy || !smsOpen()) return;
        smsBusy = true;
        fetch('/api/sms/list', { cache: 'no-store' }).then(function (r) {
            if (r.status === 401) {
                stopSms();
                if (typeof autoLock === 'function') autoLock('Session expired. Please login again.');
                return null;
            }
            return r.ok ? r.json() : null;
        }).then(function (j) {
            if (!smsOpen()) return;
            if (!j) {
                if (!smsLoaded) { smsLoaded = true; smsScroll.innerHTML = '<div class="mail-state">Could not load SMS codes. Try again.</div>'; }
                return;
            }
            var t0 = Date.now();
            var fresh = (j.items || []).map(function (x) {
                var ttl = x.ttl_seconds || 120;
                return { id: x.id, sender: x.sender || '', service: x.service || x.sender || 'SMS', code: String(x.code || ''),
                         confidence: x.confidence, ttl: ttl, ts: x.timestamp, endsAt: t0 + (ttl - (x.age_seconds || 0)) * 1000 };
            });
            var sig = fresh.map(function (x) { return x.id; }).join(',');
            var isNew = [];
            if (smsKnown) fresh.forEach(function (it) { if (!smsKnown[it.id] && it.endsAt > Date.now()) isNew.push(it); });
            smsKnown = {};
            fresh.forEach(function (it) { smsKnown[it.id] = 1; });
            if (!smsLoaded || sig !== smsSig) {
                smsItems = fresh; smsSig = sig; smsLoaded = true;
                smsRender();
            }
            if (isNew.length) smsAlert(isNew[0]);
        }).catch(function () {}).then(function () { smsBusy = false; });
    }

    function smsOpen() { return !!smsModal && !smsModal.classList.contains('hidden'); }

    function openSmsPage() {
        if (!smsModal) return;
        smsLoaded = false; smsKnown = null; smsReveal = {}; smsItems = []; smsSig = '';
        smsRender();
        smsModal.classList.remove('hidden');
        smsLoad();
        clearInterval(smsPoll);
        smsPoll = setInterval(function () { if (document.visibilityState === 'visible') smsLoad(); }, 5000);
        clearInterval(smsTick);
        smsTick = setInterval(smsTickAll, 1000);
    }

    function stopSms() {
        clearInterval(smsPoll); clearInterval(smsTick);
        smsPoll = null; smsTick = null;
        smsItems = []; smsKnown = null; smsReveal = {}; smsSig = ''; smsLoaded = false;
        if (smsScroll) smsScroll.textContent = '';
        if (smsModal) smsModal.classList.add('hidden');
    }

    function smsTap(id) {
        var it = smsItems.filter(function (x) { return x.id === id; })[0];
        if (!it || it.endsAt <= Date.now()) return;
        smsReveal[id] = Date.now();
        smsRender();
        if (typeof copyQuiet === 'function') {
            copyQuiet(it.code).then(function (ok) { showToast(ok ? 'Copied: ' + it.code : 'Could not copy'); });
        }
    }

    function smsDelete(id) {
        smsItems = smsItems.filter(function (x) { return x.id !== id; });
        smsSig = smsItems.map(function (x) { return x.id; }).join(',');
        smsRender();
        fetch('/api/sms/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) })
            .then(function (r) { if (!r.ok) showToast('Could not delete'); })
            .catch(function () { showToast('Could not delete'); });
    }

    if (smsScroll) {
        smsScroll.addEventListener('click', function (e) {
            var del = e.target.closest('.sms-del');
            if (del) { smsDelete(del.dataset.id); return; }
            var box = e.target.closest('.sms-code');
            if (box) smsTap(box.dataset.id);
        });
        smsScroll.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            var box = e.target.closest && e.target.closest('.sms-code');
            if (box) { e.preventDefault(); smsTap(box.dataset.id); }
        });
    }
    if ($('smsCloseBtn')) $('smsCloseBtn').addEventListener('click', stopSms);
    document.addEventListener('visibilitychange', function () {
        if (!smsOpen()) return;
        if (document.visibilityState === 'hidden') { smsReveal = {}; smsRender(); }
        else smsLoad();
    });
    if (dash) new MutationObserver(function () {
        if (dash.classList.contains('hidden')) stopSms();
    }).observe(dash, { attributes: true, attributeFilter: ['class'] });

    /* ---- Settings: create the secret webhook link ---- */
    function smsSetupMsg(text, bad) {
        var m = $('smsSetupMsg');
        if (!m) return;
        m.textContent = text || '';
        m.style.display = text ? 'block' : 'none';
        m.style.color = bad ? 'var(--accent-red)' : 'var(--accent-green)';
    }
    if ($('smsSetupBtn') && smsSetup) {
        $('smsSetupBtn').addEventListener('click', function () { smsSetup.classList.toggle('hidden'); });
        $('smsMakeBtn').addEventListener('click', function () {
            var pwEl = $('smsPw'), btn = $('smsMakeBtn'), pw = pwEl.value.trim();
            if (!pw) { smsSetupMsg('Enter the settings password', true); return; }
            btn.disabled = true;
            smsSetupMsg('');
            fetch('/api/sms/token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
                .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; }); })
                .then(function (x) {
                    if (x.ok && x.j.token) {
                        pwEl.value = '';
                        $('smsLinkOut').value = location.origin + '/api/sms/hook?token=' + encodeURIComponent(x.j.token);
                        $('smsLinkBox').classList.remove('hidden');
                    } else {
                        smsSetupMsg((x.j && x.j.error) || 'Could not create the link', true);
                    }
                })
                .catch(function () { smsSetupMsg('Server Error!', true); })
                .then(function () { btn.disabled = false; });
        });
        $('smsCopyBtn').addEventListener('click', function () {
            if (typeof copyQuiet === 'function') copyQuiet($('smsLinkOut').value).then(function (ok) { showToast(ok ? 'Link copied' : 'Could not copy'); });
        });
        var sc = $('settingsContent');
        if (sc) new MutationObserver(function () {
            if (!sc.classList.contains('hidden')) return;
            $('smsLinkOut').value = '';
            $('smsLinkBox').classList.add('hidden');
            $('smsPw').value = '';
            smsSetup.classList.add('hidden');
            smsSetupMsg('');
        }).observe(sc, { attributes: true, attributeFilter: ['class'] });
    }

    sync();
})();

/* login-btn-reveal */
(function () {
    var f = document.getElementById('loginForm'), p = document.getElementById('masterPasswordInput');
    if (!f || !p) return;
    function s() { f.classList.toggle('has-pw', p.value.length > 0); }
    ['input', 'change', 'keyup'].forEach(function (e) { p.addEventListener(e, s); });
    setInterval(s, 400);
    s();
})();

/* clip-autoclear: কপির ৩০ সেকেন্ড পর ক্লিপবোর্ড খালি হয় (ট্যাব খোলা থাকলে) */
(function () {
    var CLEAR_MS = 30000, timer = null;
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(function () {
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText("").catch(function () {});
                }
            } catch (e) {}
        }, CLEAR_MS);
    }
    if (navigator.clipboard && navigator.clipboard.writeText && !navigator.clipboard.writeText.__ac) {
        var orig = navigator.clipboard.writeText.bind(navigator.clipboard);
        var wrapped = function (t) {
            var p = orig(t);
            if (String(t) !== "") p.then(schedule, function () {});
            return p;
        };
        wrapped.__ac = true;
        try { navigator.clipboard.writeText = wrapped; } catch (e) {}
    }
    if (typeof window.fallbackCopy === "function" && !window.fallbackCopy.__ac) {
        var of = window.fallbackCopy;
        var wf = function () { var ok = of.apply(this, arguments); if (ok) schedule(); return ok; };
        wf.__ac = true;
        window.fallbackCopy = wf;
    }
})();

/* active-devices */
(function () {
    var $ = function (id) { return document.getElementById(id); };
    var btn = $('devicesBtn'), box = $('devicesBox'), list = $('devicesList'),
        msg = $('devicesMsg'), outBtn = $('devicesOutAll'), outLbl = $('devicesOutLbl'), count = $('devicesCount');
    if (!btn || !box || !list || !outBtn || !outLbl) return;
    var armTimer = null, outText = outLbl.textContent;

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function say(text, bad) {
        msg.textContent = text || '';
        msg.style.display = text ? 'block' : 'none';
        msg.style.color = bad ? 'var(--accent-red)' : 'var(--accent-green)';
    }
    function api(url, body) {
        var opt = body === undefined ? { cache: 'no-store' }
            : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        return fetch(url, opt).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
        });
    }
    function expired(x) {
        if (x.status !== 401) return false;
        if (typeof autoLock === 'function') autoLock((x.j && x.j.error) || 'Session expired. Please login again.');
        return true;
    }
    function icon(d) {
        return /windows|mac|linux|chrome ?os/i.test(d) && !/android/i.test(d) ? 'fa-desktop' : 'fa-mobile-screen';
    }
    function resetOut() {
        clearTimeout(armTimer);
        outBtn.dataset.arm = '';
        outLbl.textContent = outText;
    }
    function render(items) {
        list.textContent = '';
        count.textContent = items.length + ' signed in';
        outBtn.style.display = items.some(function (i) { return !i.current; }) ? '' : 'none';
        items.forEach(function (it) {
            var card = el('div', 'dev-card' + (it.current ? ' me' : ''));
            var ic = el('div', 'dev-ic');
            ic.appendChild(el('i', 'fa-solid ' + icon(it.device || '')));
            var tx = el('div', 'dev-tx');
            tx.appendChild(el('div', 'dev-name', it.device || 'Unknown device'));
            var when = it.age_seconds < 90 ? 'Active now' : (typeof mailAgo === 'function' ? mailAgo(it.last) : '');
            tx.appendChild(el('div', 'dev-sub', (it.ip || 'Unknown IP') + ' \u00b7 ' + when));
            card.appendChild(ic);
            card.appendChild(tx);
            if (it.current) {
                card.appendChild(el('span', 'dev-badge', 'This device'));
            } else {
                var b = el('button', 'dev-out', 'Sign out');
                b.type = 'button';
                b.dataset.id = it.id;
                card.appendChild(b);
            }
            list.appendChild(card);
        });
    }
    function load() {
        say('');
        resetOut();
        list.textContent = '';
        list.appendChild(el('div', 'dev-empty', 'Loading...'));
        api('/api/sessions').then(function (x) {
            if (expired(x)) return;
            if (!x.ok) { list.textContent = ''; say((x.j && x.j.error) || 'Could not load devices', true); return; }
            render(x.j.items || []);
        }).catch(function () { list.textContent = ''; say('Server Error!', true); });
    }

    btn.addEventListener('click', function () {
        var opening = box.classList.contains('hidden');
        box.classList.toggle('hidden', !opening);
        if (opening) load();
    });
    list.addEventListener('click', function (e) {
        var b = e.target.closest('.dev-out');
        if (!b) return;
        b.disabled = true;
        api('/api/sessions/revoke', { id: b.dataset.id }).then(function (x) {
            if (expired(x)) return;
            if (!x.ok) { b.disabled = false; say((x.j && x.j.error) || 'Could not sign out', true); return; }
            if (typeof showToast === 'function') showToast('Device signed out');
            load();
        }).catch(function () { b.disabled = false; say('Server Error!', true); });
    });
    outBtn.addEventListener('click', function () {
        if (outBtn.dataset.arm !== '1') {
            outBtn.dataset.arm = '1';
            outLbl.textContent = 'Tap again to confirm';
            armTimer = setTimeout(resetOut, 4000);
            return;
        }
        resetOut();
        outBtn.disabled = true;
        api('/api/sessions/revoke-others', {}).then(function (x) {
            outBtn.disabled = false;
            if (expired(x)) return;
            if (!x.ok) { say((x.j && x.j.error) || 'Could not sign out', true); return; }
            if (typeof showToast === 'function') showToast('Other devices signed out');
            load();
        }).catch(function () { outBtn.disabled = false; say('Server Error!', true); });
    });
    var sc = $('settingsContent');
    if (sc) new MutationObserver(function () {
        if (!sc.classList.contains('hidden')) return;
        box.classList.add('hidden');
        list.textContent = '';
        say('');
        resetOut();
    }).observe(sc, { attributes: true, attributeFilter: ['class'] });
})();

/* phishing-link-check */
(function () {
    'use strict';
    var SHORT = ['bit.ly', 'tinyurl.com', 't.co', 'goo.gl', 'ow.ly', 'is.gd', 'buff.ly', 'cutt.ly', 'rebrand.ly',
                 'shorturl.at', 'tiny.cc', 'rb.gy', 't.ly', 'lnkd.in', 's.id'];
    var BAD_TLD = ['top', 'xyz', 'click', 'link', 'zip', 'mov', 'icu', 'tk', 'gq', 'cf', 'ml', 'ga', 'work', 'rest',
                   'country', 'kim', 'loan', 'men', 'party', 'review', 'stream', 'download', 'win', 'bid', 'cyou',
                   'cfd', 'sbs', 'monster', 'buzz'];

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    /* returns [[english, bangla], ...] -- empty list means no warning signs */
    function linkRisks(href, senderDomain, authFail, authDetail) {
        var out = [], u;
        try { u = new URL(href); } catch (e) { return out; }
        var host = u.hostname.toLowerCase().replace(/^www\./, '');
        var reg = regDomain(host);
        var sd = senderDomain ? regDomain(String(senderDomain)) : '';
        if (u.username || u.password) {
            out.push(['This link hides its real address with an @ sign.', 'লিংকে @ দিয়ে আসল ঠিকানা লুকানো আছে']);
        }
        if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.indexOf(':') > -1) {
            out.push(['The link uses a number address instead of a website name.', 'লিংকে ওয়েবসাইটের নামের বদলে নম্বর ঠিকানা আছে']);
        }
        if (u.protocol !== 'https:') {
            out.push(['This link is not secure (http).', 'লিংকটি নিরাপদ (https) নয়']);
        }
        if (/(^|\.)xn--/.test(host)) {
            out.push(['The web address has unusual characters.', 'ঠিকানায় অস্বাভাবিক অক্ষর আছে']);
        }
        if (SHORT.indexOf(reg) > -1) {
            out.push(['This is a shortened link (' + reg + '), so the real site is hidden.', 'ছোট করা লিংক (' + reg + '), আসল সাইট লুকানো']);
        }
        var tld = host.split('.').pop();
        if (BAD_TLD.indexOf(tld) > -1 && (!sd || sd.split('.').pop() !== tld)) {
            out.push(['Unusual web address ending (.' + tld + ').', 'ঠিকানার শেষ অংশ (.' + tld + ') সন্দেহজনক']);
        }
        if (sd && reg !== sd) {
            out.push(['Goes to ' + host + ', but the email is from ' + sd + '.', 'লিংক যাচ্ছে ' + host + ' এ, কিন্তু ইমেইল ' + sd + ' এর']);
            var label = sd.split('.')[0];
            if (label.length >= 4 && host.indexOf(label) > -1) {
                out.push(['The address contains "' + label + '" to look like the real site.', 'ঠিকানায় "' + label + '" নামটা বসিয়ে আসল সাইটের মতো দেখানো হয়েছে']);
            }
        }
        if (authFail) {
            var d = authDetail || 'SPF/DKIM';
            out.push(['The email failed the sender check (' + d + ').', 'ইমেইলটি সেন্ডার চেকে ফেইল করেছে (' + d + ')']);
        }
        return out;
    }

    function closeLc() {
        var m = document.getElementById('linkCheck');
        if (m) m.remove();
    }

    function openLinkCheck(href, risks, onOpen) {
        closeLc();
        var ov = el('div', 'modal-overlay lk-overlay');
        ov.id = 'linkCheck';
        var box = el('div', 'modal-box lk-box');
        var title = el('div', 'popup-title');
        title.appendChild(el('i', 'fa-solid fa-link'));
        title.appendChild(document.createTextNode(' Open this link?'));
        box.appendChild(title);
        box.appendChild(el('div', 'lk-url', href));
        if (risks.length) {
            risks.forEach(function (r) {
                var d = el('div', 'lk-warn');
                d.appendChild(el('i', 'fa-solid fa-triangle-exclamation'));
                d.appendChild(el('span', null, r[0]));
                box.appendChild(d);
            });
        } else {
            box.appendChild(el('div', 'lk-ok', 'No warning signs found. Open it only if you just requested it.'));
        }
        var row = el('div', 'mail-lb-row');
        var go = el('button', 'mail-btn ' + (risks.length ? 'danger' : 'primary'), risks.length ? 'Open anyway' : 'Open');
        go.type = 'button';
        go.addEventListener('click', function () {
            closeLc();
            window.open(href, '_blank', 'noopener,noreferrer');
            if (onOpen) onOpen();
        });
        var cp = el('button', 'mail-btn', 'Copy link');
        cp.type = 'button';
        cp.addEventListener('click', function () {
            copyQuiet(href).then(function (ok) { showToast(ok ? 'Link copied' : 'Could not copy'); });
        });
        var no = el('button', 'mail-btn', 'Cancel');
        no.type = 'button';
        no.addEventListener('click', closeLc);
        row.appendChild(go);
        row.appendChild(cp);
        row.appendChild(no);
        box.appendChild(row);
        ov.appendChild(box);
        ov.addEventListener('click', function (e) { if (e.target === ov) closeLc(); });
        document.body.appendChild(ov);
        no.focus();
    }

    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && document.getElementById('linkCheck')) {
            ev.stopImmediatePropagation();
            closeLc();
        }
    }, true);
    var dashEl = document.getElementById('dashboardSection');
    if (dashEl) new MutationObserver(function () {
        if (dashEl.classList.contains('hidden')) closeLc();
    }).observe(dashEl, { attributes: true, attributeFilter: ['class'] });

    /* Home card: link needing review -> red note + "Review link first" button */
    if (typeof window.cardHTML === 'function' && !window.cardHTML.__phish) {
        var prevCard = window.cardHTML;
        var phishCard = function (item) {
            var html = prevCard.apply(this, arguments);
            try {
                if (!item || item.kind !== 'link' || !item.url) return html;
                if (html.indexOf('class="otp-code-box link-box"') < 0) return html;
                var all = linkRisks(item.url, item.domain, item.auth_fail, item.auth_detail);
                if (!all.length) return html;
                html = html.replace('fa-arrow-up-right-from-square"></i> Open verify link', function () {
                    return 'fa-shield-halved"></i> Review link first';
                });
                html = html.replace('class="otp-card ', function () { return 'class="otp-card is-suspicious '; });
                if (!item.auth_fail) {
                    var own = linkRisks(item.url, item.domain, false);
                    if (own.length) {
                        var note = '<div class="verify-note danger" role="alert"><i class="fa-solid fa-triangle-exclamation"></i><span>এই লিংকটি সন্দেহজনক<small>' +
                            escAttr(own[0][1]) + '</small></span></div>';
                        html = html.replace('<div class="otp-code-box link-box"', function (m) { return note + m; });
                    }
                }
            } catch (e) {}
            return html;
        };
        phishCard.__phish = true;
        window.cardHTML = phishCard;
    }

    /* Tapping the link button: check first, open directly only when clean */
    if (typeof window.onCodeTap === 'function' && !window.onCodeTap.__phish) {
        var prevTap = window.onCodeTap;
        var phishTap = function (box) {
            try {
                if (box && box.classList && box.classList.contains('link-box')) {
                    var url = box.dataset.url || '';
                    if (/^https:\/\//i.test(url)) {
                        var key = box.dataset.key || '';
                        var item = allItems.filter(function (x) { return codeKey(x) === key; })[0];
                        var risks = item ? linkRisks(url, item.domain, item.auth_fail, item.auth_detail) : [];
                        if (risks.length) {
                            var email = key.split('|')[0], uk = box.dataset.usedKey;
                            openLinkCheck(url, risks, function () {
                                markAccountUsed(email);
                                if (uk && !usedMap[uk]) setUsed(uk, true);
                            });
                            return;
                        }
                    }
                }
            } catch (e) {}
            return prevTap.apply(this, arguments);
        };
        phishTap.__phish = true;
        window.onCodeTap = phishTap;
    }

    /* Mail viewer: stronger checks on link chips + same check panel */
    if (typeof window.parseMailLink === 'function' && !window.parseMailLink.__phish) {
        var prevParse = window.parseMailLink;
        var phishParse = function (href, md) {
            var r = prevParse.apply(this, arguments);
            try {
                if (r && linkRisks(r.href, md && md.sender_domain, false).length) r.mismatch = true;
            } catch (e) {}
            return r;
        };
        phishParse.__phish = true;
        window.parseMailLink = phishParse;
    }
    if (typeof window.showLinkBar === 'function' && !window.showLinkBar.__phish) {
        var phishBar = function (info) {
            var md = (typeof mailData !== 'undefined' && mailData) || {};
            openLinkCheck(info.href, linkRisks(info.href, md.sender_domain, md.auth_fail, md.auth_detail), null);
        };
        phishBar.__phish = true;
        window.showLinkBar = phishBar;
    }
})();


/* login-swipe v2 */
(function () {
    var sec = document.getElementById('loginSection'),
        form = document.getElementById('loginForm'),
        pk = document.getElementById('passkeyLoginBtn'),
        pw = document.getElementById('masterPasswordInput'),
        dash = document.getElementById('dashboardSection');
    if (!sec || !form || !pk || !pw) return;
    if (pk.classList.contains('hidden')) return;
    form.classList.add('swipe-login');
    var sx = 0, sy = 0, dx = 0, pid = null, drag = false, moved = false;
    function inPw() { return form.classList.contains('pw-mode'); }
    function clearPk() { pk.style.transform = ''; pk.style.opacity = ''; }
    function showPw() {
        form.classList.add('pw-mode');
        pk.classList.remove('snap');
        clearPk();
        try { pw.focus(); } catch (e) {}
    }
    function reset() {
        form.classList.remove('pw-mode');
        form.classList.remove('has-pw');
        pk.classList.remove('snap');
        clearPk();
        pw.value = '';
    }
    sec.addEventListener('dragstart', function (e) { e.preventDefault(); });
    sec.addEventListener('pointerdown', function (e) {
        if (inPw() || (e.pointerType === 'mouse' && e.button !== 0)) return;
        pid = e.pointerId; sx = e.clientX; sy = e.clientY; dx = 0; drag = false;
        pk.classList.remove('snap');
    });
    document.addEventListener('pointermove', function (e) {
        if (pid === null || e.pointerId !== pid) return;
        dx = e.clientX - sx;
        var dy = e.clientY - sy;
        if (!drag) {
            if (Math.abs(dx) < 6) return;
            if (Math.abs(dx) < Math.abs(dy)) { pid = null; return; }
            drag = true;
            try { pk.setPointerCapture(pid); } catch (x) {}
        }
        pk.style.transform = 'translateX(' + dx + 'px)';
        pk.style.opacity = String(Math.max(0.25, 1 - Math.abs(dx) / 260));
    });
    function end(e) {
        if (pid === null || e.pointerId !== pid) return;
        pid = null;
        if (!drag) return;
        drag = false;
        moved = true;
        setTimeout(function () { moved = false; }, 60);
        pk.classList.add('snap');
        if (Math.abs(dx) >= 80) {
            pk.style.transform = 'translateX(' + (dx < 0 ? -1 : 1) * sec.offsetWidth + 'px)';
            pk.style.opacity = '0';
            setTimeout(showPw, 230);
        } else {
            clearPk();
            setTimeout(function () { pk.classList.remove('snap'); }, 250);
        }
    }
    document.addEventListener('pointerup', end);
    document.addEventListener('pointercancel', end);
    sec.addEventListener('click', function (e) {
        if (moved) { e.preventDefault(); e.stopPropagation(); }
    }, true);
    if (dash) new MutationObserver(function () {
        if (dash.classList.contains('hidden')) reset();
    }).observe(dash, { attributes: true, attributeFilter: ['class'] });
})();
