"""Phone notifications via ntfy (https://ntfy.sh) when the traffic light changes."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime

from . import analysis
from .config import Config, Notifications
from .db import DB

log = logging.getLogger("notify")
CHECK_EVERY_S = 60


def send(n: Notifications, title: str, message: str, tags: list[str] | None = None, priority: int = 3) -> None:
    """Publish one message. JSON publishing keeps ä/ö and € intact (HTTP headers can't)."""
    body = {"topic": n.topic, "title": title, "message": message, "tags": tags or [], "priority": priority}
    if n.click_url:
        body["click"] = n.click_url
    req = urllib.request.Request(n.server + "/", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "sahkovahti/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


class Notifier(threading.Thread):
    """Checks the light every minute and notifies once per green (or red) period.

    A period counts only if it lasts at least min_minutes. During quiet hours nothing is sent;
    if the period is still going (with enough time left) when quiet hours end, it is sent then.
    """

    def __init__(self, cfg: Config, db: DB):
        super().__init__(daemon=True, name="notify")
        self.cfg, self.db = cfg, db
        self.started = False
        self.level: str | None = None
        self.done = True
        self.last_sent: dict | None = None
        self.last_error: str | None = None

    def check(self, now: int | None = None) -> dict | None:
        """One step; returns the message sent (or None). Separate from run() for tests."""
        now = int(time.time()) if now is None else now
        n = self.cfg.notify
        L = analysis.traffic_light(self.cfg, self.db, now)
        if not self.started:       # don't notify about the period already going at start-up
            self.started, self.level = True, L["level"]
            return None
        if L["level"] != self.level:
            self.level, self.done = L["level"], False
        if self.done or not n.enabled or not n.topic:
            return None
        wanted = {"green": n.on_green, "red": n.on_red}.get(self.level)
        if not wanted:
            self.done = True
            return None
        local = datetime.fromtimestamp(now, self.cfg.tz)
        if n.is_quiet(local.hour):
            return None          # try again after quiet hours
        until = L["next"]["at"] if L["next"] else None
        if until is not None and (until - now) / 60 < n.min_minutes:
            self.done = True     # too short to bother (and it only gets shorter)
            return None
        msg = self._message(L, until)
        self.done = True
        try:
            send(n, **msg)
            self.last_sent, self.last_error = {"at": now, **msg}, None
            log.info("sent: %s", msg["title"])
        except Exception as e:  # network down, bad server — don't retry this period
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("notification failed: %s", self.last_error)
        return msg

    def _message(self, L: dict, until: int | None) -> dict:
        hm = lambda t: datetime.fromtimestamp(t, self.cfg.tz).strftime("%H:%M")  # noqa: E731
        price = f"{L['price']:.1f}".replace(".", ",") + " c/kWh" if L["price"] is not None else ""
        how_long = f"until {hm(until)}" if until else "for all prices published so far"
        if self.level == "green":
            return {"title": "Green: good time to use electricity",
                    "message": f"Price {price}, green {how_long}. Sauna, EV charging, laundry – go ahead.",
                    "tags": ["green_circle"], "priority": 3}
        return {"title": "Red: electricity is expensive",
                "message": f"Price {price}, red {how_long}. Postpone big loads if you can.",
                "tags": ["red_circle"], "priority": 3}

    def run(self):
        while True:
            try:
                self.check()
            except Exception:
                log.exception("notifier check failed")
            time.sleep(CHECK_EVERY_S)
