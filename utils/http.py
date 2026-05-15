"""
utils/http.py
Shared HTTP session with retry logic, rate limiting, and per-domain headers.
All scrapers import get_session() from here — never create bare requests.get().
"""

import time
import logging
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import (
    REQUEST_DELAY, REQUEST_TIMEOUT, MAX_RETRIES,
    DEFAULT_HEADERS, HOUSE_HEADERS
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

RETRY_BACKOFF    = [2, 5, 10] 

log = logging.getLogger(__name__)

_last_request_time: dict[str, float] = {}


def _build_session(headers: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(headers)

    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=2.0,
        status_forcelist={429, 500, 502, 503, 504},
        allowed_methods={"GET", "POST"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_senate_session  = _build_session(DEFAULT_HEADERS)
_house_session   = _build_session(HOUSE_HEADERS)
_revisor_session = _build_session(DEFAULT_HEADERS)
_mec_session     = _build_session(HOUSE_HEADERS)   # MEC also wants browser headers
_default_session = _build_session(DEFAULT_HEADERS)


def _get_session_for_url(url: str) -> requests.Session:
    host = urlparse(url).netloc.lower()
    if "house.mo.gov" in host:
        return _house_session
    if "senate.mo.gov" in host:
        return _senate_session
    if "revisor.mo.gov" in host:
        return _revisor_session
    if "mec.mo.gov" in host:
        return _mec_session
    return _default_session


def _rate_limit(domain: str):
    last = _last_request_time.get(domain, 0)
    wait = REQUEST_DELAY - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_time[domain] = time.monotonic()


def fetch(url: str, params: dict = None, method: str = "GET",
          data: dict = None, timeout: int = None) -> requests.Response | None:
    domain = urlparse(url).netloc
    _rate_limit(domain)

    session = _get_session_for_url(url)
    t = timeout or REQUEST_TIMEOUT

    try:
        if method.upper() == "POST":
            resp = session.post(url, params=params, data=data, timeout=t)
        else:
            resp = session.get(url, params=params, timeout=t)

        if resp.status_code == 403:
            log.warning(f"403 Forbidden: {url} — check headers")
            return None
        if resp.status_code == 404:
            log.debug(f"404 Not Found: {url}")
            return None
        if not resp.ok:
            log.warning(f"HTTP {resp.status_code}: {url}")
            return None

        log.debug(f"OK {resp.status_code}: {url}")
        return resp

    except requests.RequestException as e:
        log.error(f"Request failed: {url} — {e}")
        return None


def fetch_html(url: str, params: dict = None) -> str | None:
    resp = fetch(url, params=params)
    return resp.text if resp else None


def fetch_bytes(url: str, params: dict = None) -> bytes | None:
    resp = fetch(url, params=params)
    return resp.content if resp else None