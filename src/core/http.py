# src/core/http.py
from __future__ import annotations

import logging
import time
from contextlib import suppress

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_S = (0.5, 1.0, 2.0)


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


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT, *, force_browser_headers: bool = False) -> bytes:
    """
    GET robusto:
    - Usa requests.Session (cookies + keep-alive)
    - Faz warm-up antes de pedir páginas do diariodarepublica.pt
    - Retries/backoff simples (3 tentativas)
    - Mensagens claras para 403/429/5xx
    - Se vier "HTML shell OutSystems" num /dr/detalhe/, tenta 2ª vez com headers browser + Referer
    """
    if "diariodarepublica.pt" in (url or ""):
        _warmup()

    headers = HEADERS_BROWSER if force_browser_headers else HEADERS_BASE
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)

            if r.status_code in (403, 429):
                raise FetchError(
                    url,
                    f"Bloqueio ou rate-limit (HTTP {r.status_code}). Tenta novamente mais tarde.",
                    status_code=r.status_code,
                )
            if 500 <= r.status_code < 600:
                raise FetchError(
                    url,
                    f"Servidor indisponível (HTTP {r.status_code}).",
                    status_code=r.status_code,
                )
            if r.status_code >= 400:
                raise FetchError(url, f"HTTP {r.status_code}", status_code=r.status_code)

            content = r.content

            # Retry quando apanha shell (especialmente em /dr/detalhe/)
            if ("/dr/detalhe/" in url) and _looks_like_outsystems_shell(content):
                headers2 = dict(HEADERS_BROWSER)
                headers2["Referer"] = "https://diariodarepublica.pt/dr/home"
                _warmup()

                r2 = SESSION.get(url, headers=headers2, timeout=timeout, allow_redirects=True)
                if r2.status_code in (403, 429):
                    raise FetchError(
                        url,
                        f"Bloqueio ou rate-limit (HTTP {r2.status_code}). Tenta novamente mais tarde.",
                        status_code=r2.status_code,
                    )
                if 500 <= r2.status_code < 600:
                    raise FetchError(
                        url,
                        f"Servidor indisponível (HTTP {r2.status_code}).",
                        status_code=r2.status_code,
                    )
                if r2.status_code >= 400:
                    raise FetchError(url, f"HTTP {r2.status_code}", status_code=r2.status_code)
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


__all__ = [
    "FetchError",
    "http_get",
    "SESSION",
    "HEADERS_BASE",
    "HEADERS_BROWSER",
    "_looks_like_outsystems_shell",
]
