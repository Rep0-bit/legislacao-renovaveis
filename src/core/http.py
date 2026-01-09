# src/core/http.py
from __future__ import annotations

import logging
from contextlib import suppress

import requests

logger = logging.getLogger(__name__)


# -----------------------------
# Headers / Session (cookies + warm-up)
# -----------------------------
HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0",
}

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

SESSION = requests.Session()
SESSION.headers.update(HEADERS_BROWSER)

_WARMED_UP = False


def _warmup() -> None:
    """Pedido inicial para obter cookies/sessão (evita shell OutSystems em alguns casos)."""
    global _WARMED_UP
    if _WARMED_UP:
        return
    with suppress(Exception):
        SESSION.get("https://diariodarepublica.pt/dr/home", timeout=30, allow_redirects=True)
    _WARMED_UP = True


# -----------------------------
# Exceptions
# -----------------------------
class FetchError(RuntimeError):
    def __init__(self, url: str, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.url = url
        self.status_code = status_code


# -----------------------------
# Helpers HTTP
# -----------------------------
def _looks_like_outsystems_shell(html: bytes) -> bool:
    """
    Deteta a shell mínima do OutSystems (reactContainer + scripts OutSystems),
    que NÃO contém o conteúdo do diploma nem links PDF.
    """
    s = html.decode("utf-8", errors="ignore")

    has_react = (
        ('id="reactContainer"' in s) or ("OutSystemsReactView.js" in s) or ("window.OutSystemsApp" in s)
    )
    if not has_react:
        return False

    # Se já há sinais de conteúdo, não é shell
    return not (("SUMÁRIO" in s) or ("TEXTO" in s) or ("Data de Publicação" in s) or ("<h1" in s.lower()))


def http_get(url: str, timeout: int = 60, *, force_browser_headers: bool = False) -> bytes:
    """
    GET robusto:
    - Usa requests.Session (cookies + keep-alive)
    - Faz warm-up antes de pedir páginas do diariodarepublica.pt
    - Se vier "HTML shell OutSystems" num /dr/detalhe/, tenta 2ª vez com headers browser + Referer
    """
    if "diariodarepublica.pt" in (url or ""):
        _warmup()

    headers = HEADERS_BROWSER if force_browser_headers else HEADERS_BASE

    try:
        r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            raise FetchError(url, f"HTTP {r.status_code}", status_code=r.status_code)

        content = r.content

        # Retry quando apanha shell (especialmente em /dr/detalhe/)
        if ("/dr/detalhe/" in url) and _looks_like_outsystems_shell(content):
            headers2 = dict(HEADERS_BROWSER)
            headers2["Referer"] = "https://diariodarepublica.pt/dr/home"
            _warmup()

            r2 = SESSION.get(url, headers=headers2, timeout=timeout, allow_redirects=True)
            if r2.status_code >= 400:
                raise FetchError(url, f"HTTP {r2.status_code}", status_code=r2.status_code)
            return r2.content

        return content

    except requests.exceptions.Timeout as e:
        logger.debug("⏱️ Timeout a pedir %s (timeout=%ss)", url, timeout)
        raise FetchError(url, f"Timeout ({timeout}s)") from e
    except requests.exceptions.RequestException as e:
        logger.debug("🌐 RequestException a pedir %s: %s", url, e)
        raise FetchError(url, f"RequestException: {e}") from e


__all__ = [
    "FetchError",
    "http_get",
    "SESSION",
    "HEADERS_BASE",
    "HEADERS_BROWSER",
    "_looks_like_outsystems_shell",
]
