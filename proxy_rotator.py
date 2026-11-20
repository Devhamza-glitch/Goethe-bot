from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Dict, List, Optional

import requests


def load_proxy_list() -> List[str]:
    """Build the proxy pool from env vars (never hardcode credentials).

    1. ``PROXY_LIST`` -- comma-separated proxy URLs, used as-is.
    2. ``DATAIMPULSE_USER`` + ``DATAIMPULSE_PASS`` -- if set, generates
       ``PROXY_SESSIONS`` (default 5) sticky Pakistan sessions on DataImpulse,
       each a different residential IP. ``DATAIMPULSE_COUNTRY`` (default ``pk``),
       ``DATAIMPULSE_HOST`` / ``DATAIMPULSE_PORT`` and ``PROXY_SESSION_MINUTES``
       (default 30) are optional.

    Both sources are merged (duplicates dropped), PROXY_LIST first.
    """
    proxies = [p.strip() for p in os.environ.get("PROXY_LIST", "").split(",") if p.strip()]

    user = os.environ.get("DATAIMPULSE_USER", "").strip()
    pw = os.environ.get("DATAIMPULSE_PASS", "").strip()
    if user and pw:
        host = os.environ.get("DATAIMPULSE_HOST", "gw.dataimpulse.com").strip()
        port = os.environ.get("DATAIMPULSE_PORT", "823").strip()
        country = os.environ.get("DATAIMPULSE_COUNTRY", "pk").strip().lower()
        minutes = os.environ.get("PROXY_SESSION_MINUTES", "30").strip()
        try:
            n = max(1, int(os.environ.get("PROXY_SESSIONS", "5")))
        except ValueError:
            n = 5
        for i in range(1, n + 1):
            proxies.append(
                f"http://{user}__cr.{country}__sessid.goethe{i}__sesstime.{minutes}:{pw}@{host}:{port}"
            )

    seen = set()
    return [p for p in proxies if not (p in seen or seen.add(p))]


class ProxyRotator:
    def __init__(self, proxy_list: Optional[List[str]] = None, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("proxy_rotator")
        raw = proxy_list or load_proxy_list()
        self._proxies: List[str] = raw
        self._blacklist: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._blacklist_duration = 300

    @property
    def available(self) -> List[str]:
        now = time.monotonic()
        with self._lock:
            return [p for p in self._proxies if p not in self._blacklist or self._blacklist[p] < now]

    def add(self, proxy: str) -> None:
        with self._lock:
            if proxy not in self._proxies:
                self._proxies.append(proxy)

    def remove(self, proxy: str) -> None:
        with self._lock:
            if proxy in self._proxies:
                self._proxies.remove(proxy)
            self._blacklist.pop(proxy, None)

    def is_healthy(self, proxy: str, timeout: int = 10) -> bool:
        try:
            resp = requests.get(
                "http://httpbin.org/ip",
                proxies={"http": proxy, "https": proxy},
                timeout=timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get(self) -> Optional[str]:
        pool = self.available
        if not pool:
            if self.logger:
                self.logger.warning("No available proxies; clearing blacklist and retrying")
            with self._lock:
                self._blacklist.clear()
                pool = self._proxies[:]
        if not pool:
            return None
        random.shuffle(pool)
        candidate = pool[0]
        if self.is_healthy(candidate):
            return candidate
        self.mark_failed(candidate)
        return self.get() if self.available else None

    def mark_failed(self, proxy: str) -> None:
        with self._lock:
            self._blacklist[proxy] = time.monotonic() + self._blacklist_duration
        if self.logger:
            self.logger.info("Proxy %s blacklisted for %ds", proxy, self._blacklist_duration)

    def mark_success(self, proxy: str) -> None:
        pass

    @property
    def count(self) -> int:
        return len(self._proxies)
