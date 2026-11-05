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
