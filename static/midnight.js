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
            if ($('accountCountBar')) $('accountCountBar').click();
            setTimeout(function () { if ($('tfaServiceBtn')) $('tfaServiceBtn').click(); }, 200);
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
    sync();
})();
