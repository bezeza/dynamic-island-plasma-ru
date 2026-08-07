# Changelog

## 0.1.0 — first release

The island takes `org.freedesktop.Notifications` away from plasmashell and
draws every notification itself, on a `wlr-layer-shell` surface pinned under
the top bezel.

**Four palettes.** True-black AMOLED by default, a dark grey body, a light
one, or the desktop's own colour scheme — switchable while it runs.

**Notifications.** Actions as buttons, KDE's `inline-reply` as a real reply
field, progress bars, replacement in place, a visible stack behind the front
card, and the accent glow computed from the sending application's icon. Click
runs the default action and brings the sender's window forward, middle-click
and swipe-up dismiss, hovering freezes the countdown.

**More than notifications.** File operations arrive over KDE's job protocol
with their pause and cancel buttons; volume, brightness and the other
on-screen displays move into the island when Plasma's own are switched off;
MPRIS gives the track that starts playing, with its cover and its buttons,
and the volume display carries the player along while music is on.

**A panel applet** keeps the last hundred notifications the pill has taken
away, reading them straight from disk so it works while the island restarts.
Clicking a row opens the application it came from. The island's own settings
live in the applet's configuration dialog.

**Sources** put cards up without being an application: RSS and Atom feeds
(polled, filtered, wearing the feed's own icon, and never replaying old news),
JSON endpoints on a timer, and a webhook other machines can push to over plain
HTTP — a URL is enough. Buttons on those cards open a link or run a command,
the second only when the configuration allows it.

**Autostart** is a systemd user unit ordered ahead of plasmashell, which is
what makes the hand-over work on every login without restarting the shell.

**Languages:** English, German, Simplified Chinese and Japanese. The island's
own words follow a setting; the applet follows the desktop language. The
settings pages are complete in German; Japanese and Chinese still carry only
the older strings.

### Known limits

* Wayland only — there is no layer shell on X11.
* Tested on one machine: Plasma 6 on Nobara/Fedora, single screen, no HiDPI.
* The history is read-only; actions and replies are gone once the card is.
* The webhook speaks plain HTTP. Behind a firewall or a VPN that is fine;
  across the internet it wants a reverse proxy in front of it.
