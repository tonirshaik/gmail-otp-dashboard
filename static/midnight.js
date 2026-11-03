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
        if (open('tfaModal')) return 'tfa';
        if (open('accountsModal')) return 'accounts';
        return 'home';
    }
    function sync() { setActive(current()); }

    function closeAll() {
        var pop = $('settingsPopup');
        if (pop && pop.classList.contains('active') && $('settingsToggle')) $('settingsToggle').click();
        ['mailModal:mailCloseBtn', 'tfaModal:tfaCloseBtn', 'accountsModal:accountsCloseBtn', 'backupModal:backupCloseBtn']
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
        } else if (name === 'settings') {
            var wasOpen = current() === 'settings';
            closeAll();
            window.scrollTo({ top: 0 });
            if (!wasOpen && $('settingsToggle')) $('settingsToggle').click();
        }
        setTimeout(sync, 260);
    });

    var mo = new MutationObserver(sync);
    ['accountsModal', 'tfaModal', 'settingsPopup', 'mailModal'].forEach(function (id) {
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

    sync();
})();
