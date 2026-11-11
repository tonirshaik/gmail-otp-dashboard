# Changelog

## v1.53.0 - 2026-11-11 (minor)
- feat: remove login pins, wandering electric lines

## v1.52.0 - 2026-11-11 (minor)
- feat: login page electric current on circuit lines

## v1.51.0 - 2026-11-11 (minor)
- feat: login pins each get one bending line

## v1.50.0 - 2026-11-11 (minor)
- feat: login circuit background 160 thin lines

## v1.49.0 - 2026-11-11 (minor)
- feat: login circuit background 14 -> 30 lines

## v1.48.1 - 2026-11-11 (patch)
- fix: merge login inner box into outer frame

## v1.48.0 - 2026-11-11 (minor)
- feat: circuit-chip style login page

## v1.47.4 - 2026-11-11 (patch)
- fix: login swipe only to the left

## v1.47.3 - 2026-11-11 (patch)
- fix: login swipe only to the right, longer drag needed

## v1.47.2 - 2026-11-11 (patch)
- fix: login swipe needs a longer drag

## v1.47.1 - 2026-11-11 (patch)
- fix: login swipe works with mouse drag, button slides

## v1.47.0 - 2026-11-11 (minor)
- feat: login swipe to reveal master password

## v1.46.0 - 2026-11-11 (minor)
- feat: stronger phishing link warning with link check panel

## v1.45.0 - 2026-11-11 (minor)
- feat: active devices list with sign out

## v1.44.0 - 2026-11-10 (minor)
- feat: telegram 2fa expired message gets New code button

## v1.43.1 - 2026-11-08 (patch)
- fix: telegram code age shows hours/days for old codes

## v1.43.0 - 2026-11-08 (minor)
- feat: faster extension autofill, avoid near-expiry 2FA codes

## v1.42.0 - 2026-11-08 (minor)
- feat: telegram command menu (code, 2fa)

## v1.41.0 - 2026-11-08 (minor)
- feat: telegram /2fa asks Gmail 2FA or Service 2FA first

## v1.40.0 - 2026-11-08 (minor)
- feat: live countdown for telegram 2FA code

## v1.39.0 - 2026-11-08 (minor)
- feat: telegram /2fa command for 2FA codes

## v1.38.1 - 2026-11-08 (patch)
- fix: show OS and browser name in login alert for desktop

## v1.38.0 - 2026-11-07 (minor)
- feat: Telegram /code shows tap buttons for each Gmail

## v1.37.0 - 2026-11-07 (minor)
- feat: clear clipboard 30s after copying a code

## v1.36.0 - 2026-11-07 (minor)
- feat: show Inbox / Spam label in Telegram code messages

## v1.35.0 - 2026-11-07 (minor)
- feat: weekly encrypted auto backup sent to Telegram

## v1.34.0 - 2026-11-07 (minor)
- feat: Telegram alert when a Gmail App Password stops working

## v1.33.0 - 2026-11-07 (minor)
- feat: Telegram /code command to get latest code

## v1.32.2 - 2026-11-07 (patch)
- fix: count login lock per real client IP

## v1.32.1 - 2026-11-07 (patch)
- fix: show real IP and phone model in login alert

## v1.32.0 - 2026-11-07 (minor)
- feat: Telegram alert on login, wrong password and lockout

## v1.31.0 - 2026-11-07 (minor)
- feat: show Login button only when password is typed

## v1.30.1 - 2026-11-07 (patch)
- fix: watch Spam folder with IDLE so spam codes arrive instantly

## v1.30.0 - 2026-11-07 (minor)
- feat: auto-delete Telegram code messages after 2 minutes

## v1.29.2 - 2026-11-07 (patch)
- chore: add Telegram debug logs

## v1.29.1 - 2026-11-07 (patch)
- perf: reuse Telegram connection and log send time

## v1.29.0 - 2026-11-07 (minor)
- feat: Telegram bot notification for new codes

## v1.28.0 - 2026-11-07 (minor)
- feat: backup codes for Gmail 2FA cards

## v1.27.0 - 2026-11-07 (minor)
- feat: 2FA service backup codes (encrypted)

## v1.26.0 - 2026-11-07 (minor)
- feat: gmail account groups (tag, filter)

## v1.25.3 - 2026-11-05 (patch)
- fix: faster extension unlock and show loading state

## v1.25.2 - 2026-11-05 (patch)
- fix: extension shows only the Google account being signed in

## v1.25.1 - 2026-11-05 (patch)
- fix: extension shows Gmail Authenticator codes on Google pages

## v1.25.0 - 2026-11-05 (minor)
- feat: SMS OTP page via webhook

## v1.24.0 - 2026-11-05 (minor)
- feat: extension manual refresh button, remove Gmail auto-polling

## v1.23.0 - 2026-11-05 (minor)
- feat: extension instant 2FA rollover + auto-refresh email code

## v1.22.0 - 2026-11-05 (minor)
- feat: extension 24-hour login token (popup unlock + lock button)

## v1.21.0 - 2026-11-05 (minor)
- feat: browser extension to copy or auto-fill the latest code

## v1.20.3 - 2026-11-05 (patch)
- fix: git bash push test

## v1.20.2 - 2026-11-04 (patch)
- fix: card refresh always leaves 2FA view

## v1.20.1 - 2026-11-04 (patch)
- fix: refresh with a new code leaves 2FA view automatically

## v1.20.0 - 2026-11-04 (minor)
- feat: blinking light on cards with a new code

## v1.19.16 - 2026-11-04 (patch)
- fix: 2FA shield button only highlights when on

## v1.19.15 - 2026-11-04 (patch)
- fix: keep wait timer inside the hourglass button

## v1.19.14 - 2026-11-03 (patch)
- fix: faster 2FA reveal (prefetch on hover/touch, request before re-re…

## v1.19.13 - 2026-11-03 (patch)
- fix: 2FA code rollover timing (exact ms remaining, next-code prefetch…

## v1.19.12 - 2026-11-03 (patch)
- fix: 2FA icon - revert wait-for-server change so the card reacts instantly again

## v1.19.11 - 2026-11-03 (patch)
- fix: 2FA cards - repaint timer bars right after re-render so old cards do not lag

## v1.19.10 - 2026-11-03 (patch)
- fix: 2FA icon - skip the three-dot loading view, show the code directly

## v1.19.9 - 2026-11-03 (patch)
- fix: phone account cards - smaller action icons and Gmail text so address fits

## v1.19.8 - 2026-11-03 (patch)
- fix: change accounts search placeholder to 'Search Gmail address...'

## v1.19.7 - 2026-11-03 (patch)
- fix: show shield icon instead of letter avatar in 2FA mode

## v1.19.6 - 2026-11-03 (patch)
- chore: restore .gitignore

## v1.19.5 - 2026-11-03 (patch)
- fix: always show Gmail address on OTP cards (css)

## v1.19.4 - 2026-11-03 (patch)
- Delete gmail-address-on-cards.patch

## v1.19.3 - 2026-11-03 (patch)
- Add files via upload

## v1.19.2 - 2026-11-03 (patch)
- Update app.py

## v1.19.1 - 2026-11-03 (patch)
- Fix Gmail address placement in OTP card

## v1.19.0 - 2026-11-03 (minor)
- Fix truncated Gmail address in OTP card header

## v1.18.0 - 2026-11-03 (minor)
- Move Gmail address into OTP card header and remove excess space

## v1.17.0 - 2026-11-03 (minor)
- Move compact account actions to the top-right of OTP cards

## v1.16.0 - 2026-11-03 (minor)
- Polish OTP cards with a smaller, cleaner layout

## v1.15.0 - 2026-11-03 (minor)
- Make OTP cards compact on mobile and desktop

## v1.14.0 - 2026-11-03 (minor)
- Redesign OTP cards to match reference screenshots

## v1.13.1 - 2026-11-03 (patch)
- perf(ui): open 2FA Services instantly (no "Loading..." flash)

## v1.13.0 - 2026-11-03 (minor)
- feat(ui): remove Quick find; test notification moves to Settings

## v1.12.0 - 2026-11-03 (minor)
- feat(ui): move toolbar buttons into Settings; short blinking "Live" b…

## v1.11.0 - 2026-11-03 (minor)
- feat(ui): Settings as a page, Add Account on Accounts page, remove Lo…

## v1.10.0 - 2026-11-02 (minor)
- feat(ui): Accounts and 2FA Services open as in-app pages, not popups

## v1.9.0 - 2026-11-02 (minor)
- feat(ui): separate Accounts and 2FA Services nav; drop duplicate top …

## v1.8.0 - 2026-11-02 (minor)
- feat(ui): hide top mail/gear buttons on phone; settings opens as bott…

## v1.7.0 - 2026-11-02 (minor)
- feat(ui): Midnight mobile-app redesign with desktop sidebar layout

## v1.6.11 - 2026-11-02 (patch)
- Update app.py

## v1.6.10 - 2026-11-02 (patch)
- Update app.py

## v1.6.9 - 2026-11-02 (patch)
- Update app.py

## v1.6.8 - 2026-11-02 (patch)
- Update app.py

## v1.6.7 - 2026-11-02 (patch)
- Update app.py

## v1.6.6 - 2026-11-02 (patch)
- Update app.py

## v1.6.5 - 2026-10-31 (patch)
- Fix Spam OTP detection in on-demand watcher

## v1.6.4 - 2026-10-31 (patch)
- Fix on-demand OTP watch for Inbox and Spam

## v1.6.3 - 2026-10-31 (patch)
- Update app.css

## v1.6.2 - 2026-10-31 (patch)
- Update app.js

## v1.6.1 - 2026-10-31 (patch)
- Remove dead code (unused functions, route, markup and CSS)

## v1.6.0 - 2026-10-31 (minor)
- Show app version + auto version bump on merge; untrack stray __pycache__
