import json
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

# -----------------------------
# CONFIG (ajusta aqui)
# -----------------------------
URL_DETALHE = "https://diariodarepublica.pt/dr/detalhe/decreto-lei/15-2022-177634016"

# Se tiveres o PDF direto (recomendado), põe aqui.
# Se não tiveres, deixa None e o script tenta encontrá-lo (nem sempre dá).
URL_PDF_DIRETO = "https://files.diariodarepublica.pt/1s/2022/01/01000/0000300185.pdf"
# URL_PDF_DIRETO = None

OUT_DIR = Path("data")
HEADERS = {"User-Agent": "Mozilla/5.0"}  # ajuda a evitar bloqueios básicos


# -----------------------------
# HELPERS
# -----------------------------
def download(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.content


def make_doc_id_from_url(url: str) -> str:
    """
    Ex: https://diariodarepublica.pt/dr/detalhe/decreto-lei/15-2022-177634016
    -> decreto-lei_15-2022_177634016
    """
    p = urlparse(url).path.rstrip("/")
    m = re.search(r"/dr/detalhe/([^/]+)/([^/]+)$", p)
    if m:
        tipo = m.group(1)  # decreto-lei
        resto = m.group(2)  # 15-2022-177634016
        return f"{tipo}_{resto}".replace("/", "_")

    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.strip("/"))
    return safe[:120] if len(safe) > 120 else safe


def find_first_pdf_link(html: bytes) -> str | None:
    """
    Procura por links diretos .pdf no HTML.
    Nota: no DR nem sempre aparece no HTML estático (por ser renderizado por JS).
    """
    s = html.decode("utf-8", errors="ignore")
    m = re.search(r"https?://files\.(?:diariodarepublica|dre)\.pt/[^\s\"']+\.pdf", s)
    if m:
        return m.group(0)
    m2 = re.search(r"https?://[^\s\"']+\.pdf", s)
    return m2.group(0) if m2 else None


def extract_visible_text_from_html(html: bytes) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def find_consolidated_link(html: bytes) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/dr/legislacao-consolidada/" in href:
            if href.startswith("http"):
                return href
            return "https://diariodarepublica.pt" + href
    return None


def pdf_to_text(pdf_path: Path) -> str:
    """
    Extrai texto do PDF com pypdf.
    Se o PDF tiver encriptação, alguns vêm "marcados" como encriptados mas sem password.
    """
    reader = PdfReader(str(pdf_path))

    if getattr(reader, "is_encrypted", False):
        with suppress(Exception):
            reader.decrypt("")  # tenta desencriptar com password vazia

    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts).strip()


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    doc_id = make_doc_id_from_url(URL_DETALHE)

    # pastas
    (OUT_DIR / "raw_html").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pdfs").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "text").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "meta").mkdir(parents=True, exist_ok=True)

    # 1) Baixa página de detalhe
    print("🔎 Baixando página de detalhe...")
    detalhe_html = download(URL_DETALHE)
    (OUT_DIR / "raw_html" / f"{doc_id}__detalhe.html").write_bytes(detalhe_html)

    # 2) Determina URL do PDF
    pdf_url = URL_PDF_DIRETO or find_first_pdf_link(detalhe_html)
    if not pdf_url:
        print("⚠️ (PDF) Não encontrei link de PDF no HTML.")
        print(
            "   Sugestão: no site, clica em 'PDF' e copia o link direto (files.diariodarepublica.pt / files.dre.pt)."
        )
        pdf_path = None
        pdf_text_path = None
        pdf_text_ok = False
    else:
        # 3) Baixa PDF
        print("📥 Baixando PDF...")
        pdf_bytes = download(pdf_url)
        pdf_path = OUT_DIR / "pdfs" / f"{doc_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        # 4) Extrai texto do PDF
        print("📄 Extraindo texto do PDF...")
        pdf_text = pdf_to_text(pdf_path)
        pdf_text_path = OUT_DIR / "text" / f"{doc_id}__pdf.txt"
        pdf_text_path.write_text(pdf_text, encoding="utf-8")
        pdf_text_ok = bool(pdf_text.strip())

    # 5) Consolidado (se existir)
    print("🔎 Tentando encontrar consolidado na página…")
    cons_url = find_consolidated_link(detalhe_html)
    cons_text_ok = False
    cons_text_path = None
    if cons_url:
        cons_html = download(cons_url)
        (OUT_DIR / "raw_html" / f"{doc_id}__consolidado.html").write_bytes(cons_html)
        cons_text = extract_visible_text_from_html(cons_html)
        cons_text_path = OUT_DIR / "text" / f"{doc_id}__consolidado.txt"
        cons_text_path.write_text(cons_text, encoding="utf-8")
        cons_text_ok = bool(cons_text.strip())
        print(f"👉 Consolidado encontrado: {cons_url}")
    else:
        print("⚠️ (Consolidado) Não encontrado no HTML (pode acontecer).")

    # 6) Metadados
    meta = {
        "doc_id": doc_id,
        "url_detalhe": URL_DETALHE,
        "url_pdf": pdf_url,
        "url_consolidado": cons_url,
        "ficheiro_pdf": str(pdf_path) if pdf_path else None,
        "ficheiro_texto_pdf": str(pdf_text_path) if pdf_text_path else None,
        "ficheiro_texto_consolidado": str(cons_text_path) if cons_text_path else None,
        "tem_texto_pdf": pdf_text_ok,
        "tem_texto_consolidado": cons_text_ok,
    }
    meta_path = OUT_DIR / "meta" / f"{doc_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7) Resumo
    print("\n✅ OK! Guardado em:")
    print(f"- {meta_path}")
    if pdf_path:
        print(f"- {pdf_path}")
    if pdf_text_path:
        print(f"- {pdf_text_path}")
    if cons_text_path:
        print(f"- {cons_text_path}")
    print(f"\n📌 doc_id = {doc_id}")


if __name__ == "__main__":
    main()
