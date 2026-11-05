(function () {
  "use strict";
  if (location.protocol !== "https:" || location.origin === "@@ORIGIN@@" || window.top !== window) return;

  var CODE_RX = /otp|one.?time|verif|2fa|mfa|token|passcode|security.?code|auth.?code|login.?code|confirm.?code|\bpin\b|\bcode\b|কোড/i;
  var BAD_RX = /zip|postal|promo|coupon|voucher|search|captcha|referral|gift|discount|card|cvv|cvc|address|country|invite/i;
  var CODE_OK = /^[A-Za-z0-9]{3,12}$/;
  var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

  var timer = null, tries = 0, quit = false, busy = false, obs = null, deb = null;

  function visible(el) {
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  function usable(el) {
    return /^(text|tel|number)$/.test(el.type) && !el.disabled && !el.readOnly && visible(el);
  }
  function labelText(el) {
    var t = "";
    try {
      t = Array.prototype.map.call(el.labels || [], function (l) { return l.textContent; }).join(" ");
    } catch (e) {}
    return [el.name, el.id, el.placeholder, el.getAttribute("aria-label"), t].join(" ");
  }
  function findTargets() {
    var all = Array.prototype.slice.call(document.querySelectorAll("input")).filter(usable);
    var boxes = all.filter(function (e) { return e.maxLength === 1; });
    if (boxes.length >= 4 && boxes.length <= 10) return { multi: boxes };
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.maxLength > 10) continue;
      var ac = (e.getAttribute("autocomplete") || "").toLowerCase();
      var t = labelText(e);
      if (ac === "one-time-code" || (CODE_RX.test(t) && !BAD_RX.test(t))) return { single: e };
    }
    return null;
  }
  function setVal(el, v) {
    setter.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function isEmpty(t) {
    return t.multi ? t.multi.every(function (e) { return !e.value; }) : !t.single.value;
  }
  function fill(t, code, force) {
    if (!force && !isEmpty(t)) return false;
    if (t.multi) {
      if (code.length > t.multi.length) return false;
      t.multi.forEach(function (e, i) { setVal(e, code.charAt(i)); });
      return true;
    }
    if (t.single.maxLength > 0 && code.length > t.single.maxLength) return false;
    setVal(t.single, code);
    return true;
  }

  function pageEmail() {
    if (!/(^|\.)google\.com$/.test(location.hostname)) return "";
    var found = {}, i;
    var els = document.querySelectorAll("[data-email],[data-identifier]");
    for (i = 0; i < els.length; i++) {
      var v = (els[i].getAttribute("data-email") || els[i].getAttribute("data-identifier") || "").toLowerCase();
      if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) found[v] = 1;
    }
    if (!Object.keys(found).length) {
      var m = (document.body.innerText || "").match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [];
      for (i = 0; i < m.length; i++) found[m[i].toLowerCase()] = 1;
    }
    var k = Object.keys(found);
    return k.length === 1 ? k[0] : "";
  }
  function stop(finish) {
    if (timer) { clearInterval(timer); timer = null; }
    if (finish) {
      quit = true;
      if (obs) obs.disconnect();
    }
  }
  function ask() {
    return new Promise(function (res) {
      try {
        chrome.runtime.sendMessage({ type: "get", hint: pageEmail() }, function (r) {
          void chrome.runtime.lastError;
          res(r);
        });
      } catch (e) { res(null); }
    });
  }
  async function tick() {
    if (busy || quit) return;
    var t = findTargets();
    if (!t) return;
    busy = true;
    try {
      var r = await ask();
      if (!r) return;
      if (r.status === 401) { stop(true); return; }
      if (!r.ok) return;
      var d = r.data || {};
      var code = "";
      if (d.email && !d.expired && d.email.confidence === "high") code = d.email.code;
      else if (d.tfa && d.tfa.length === 1) code = d.tfa[0].code;
      if (code && CODE_OK.test(code) && fill(t, code, false)) stop(true);
    } finally {
      busy = false;
    }
  }
  function start() {
    if (timer || quit) return;
    if (!findTargets()) return;
    tries = 0;
    tick();
    timer = setInterval(function () {
      tries++;
      if (tries > 9) { stop(true); return; }
      tick();
    }, 10000);
  }

  chrome.runtime.onMessage.addListener(function (m, s, send) {
    if (m && m.type === "email") { send({ email: pageEmail() }); return; }
    if (m && m.type === "fill" && typeof m.code === "string" && CODE_OK.test(m.code)) {
      var t = findTargets();
      send({ ok: !!(t && fill(t, m.code, true)) });
    }
  });

  obs = new MutationObserver(function () {
    clearTimeout(deb);
    deb = setTimeout(start, 600);
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  start();
})();
