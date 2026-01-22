# src/core/http.py
from __future__ import annotations

import logging
import re
import time
from contextlib import suppress
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_S = (0.5, 1.0, 2.0)

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

HEADERS_BOT = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS_BROWSER)

_WARMED_UP = False

RE_ABS_PDF = re.compile(r"https?://[^\s\"']+?\.pdf(?:\?[^\s\"']+)?", re.I)
RE_REL_PDF = re.compile(r"(?:(?:href|src)\s*=\s*[\"'])([^\"']+?\.pdf(?:\?[^\"']+)?)", re.I)


def _warmup() -> None:
    global _WARMED_UP
    if _WARMED_UP:
        return
    with suppress(Exception):
        SESSION.get("https://diariodarepublica.pt/dr/home", timeout=30, allow_redirects=True)
    _WARMED_UP = True


class FetchError(RuntimeError):
    def __init__(self, url: str, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.url = url
        self.status_code = status_code


def _looks_like_outsystems_shell(html: bytes) -> bool:
    s = html.decode("utf-8", errors="ignore")
    if "window.OutSystemsApp" in s or "OutSystemsManifestLoader" in s or "/dr/scripts/OutSystems" in s:
        return not (("SUMÁRIO" in s) or ("TEXTO" in s) or ("Data de Publicação" in s) or ("# " in s))
    return False


def http_get(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    force_browser_headers: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    if "diariodarepublica.pt" in (url or ""):
        _warmup()

    headers = dict(HEADERS_BROWSER) if force_browser_headers else None
    if headers is not None and extra_headers:
        headers.update(extra_headers)

    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)

            if r.status_code in (403, 429):
                raise FetchError(
                    url, f"Bloqueio ou rate-limit (HTTP {r.status_code}).", status_code=r.status_code
                )
            if 500 <= r.status_code < 600:
                raise FetchError(
                    url, f"Servidor indisponível (HTTP {r.status_code}).", status_code=r.status_code
                )
            if r.status_code >= 400:
                raise FetchError(url, f"HTTP {r.status_code}", status_code=r.status_code)

            content = r.content

            if "/dr/detalhe/" in url and _looks_like_outsystems_shell(content):
                h2 = dict(HEADERS_BOT)
                if extra_headers:
                    h2.update(extra_headers)
                r2 = SESSION.get(url, headers=h2, timeout=timeout, allow_redirects=True)
                if r2.status_code < 400 and r2.content:
                    return r2.content

            return content

        except FetchError as e:
            last_exc = e
            logger.warning("🌐 %s (tentativa %s/%s)", e, attempt, MAX_RETRIES)
        except requests.exceptions.Timeout as e:
            last_exc = e
            logger.warning("⏱️ Timeout (%ss) %s (tentativa %s/%s)", timeout, url, attempt, MAX_RETRIES)
        except requests.exceptions.RequestException as e:
            last_exc = e
            logger.warning("🌐 Erro de rede %s (tentativa %s/%s)", e, attempt, MAX_RETRIES)

        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_S[attempt - 1])

    raise FetchError(url, f"Falha após {MAX_RETRIES} tentativas") from last_exc


def http_get_pdf(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    headers_pdf = {
        "Accept": "application/pdf,*/*;q=0.8",
        "Referer": "https://diariodarepublica.pt/dr/home",
    }

    b = http_get(url, timeout=timeout, force_browser_headers=True, extra_headers=headers_pdf)
    if b.lstrip().startswith(b"%PDF-"):
        return b

    s = b.decode("utf-8", errors="ignore")
    abs_links = RE_ABS_PDF.findall(s)
    rel_links = [urljoin(url, u) for u in RE_REL_PDF.findall(s)]

    candidates: list[str] = []
    seen: set[str] = set()
    for u in abs_links + rel_links:
        if u not in seen:
            candidates.append(u)
            seen.add(u)

    for cand in candidates[:20]:
        b2 = http_get(cand, timeout=timeout, force_browser_headers=True, extra_headers=headers_pdf)
        if b2.lstrip().startswith(b"%PDF-"):
            return b2

    return b


__all__ = [
    "FetchError",
    "http_get",
    "http_get_pdf",
    "SESSION",
    "HEADERS_BROWSER",
    "HEADERS_BOT",
]
