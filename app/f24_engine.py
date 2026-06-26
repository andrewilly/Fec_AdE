"""
Download dei modelli F24 BOLLI (imposta di bollo) dall'Agenzia delle Entrate.

Usa la sessione autenticata (dopo login e get_b2b_tokens) per interrogare
le API del servizio "Fatture e Corrispettivi" (ivaservizi) e scaricare
i modelli F24 precompilati per l'imposta di bollo, organizzati per
trimestre e anno.

Endpoint reali (da FeCscraper — socrat3/FeCscraper):
  Elenco:  GET  /cons/cons-services/rs/fe/bollo/elenco/X/{ANNO}/{TRIMESTRE}
  Stampa:  POST /cons/cons-services/rs/fe/bollo/stampa/F24

NOTA: Questa implementazione gestisce SOLO l'imposta di bollo (F24 bolli).
Per gli F24 generici (tasse, contributi) vedi app/f24_generici_placeholder.py
che richiede l'integrazione con il Cassetto Fiscale (sistema separato).
"""

import json
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

from app.engine import unix_ms
from app.security import get_ca_bundle

# ─── Costanti ───────────────────────────────────────────────────────────────────
BASE_URL = "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs"
BOLLO_LIST_ENDPOINT = BASE_URL + "/fe/bollo/elenco"
BOLLO_PRINT_ENDPOINT = BASE_URL + "/fe/bollo/stampa/F24"
BOLLO_DETTAGLIO_ENDPOINT = BASE_URL + "/fe/bollo/dettaglio"

# ─── Funzioni principali ────────────────────────────────────────────────────────


def fetch_bollo_list(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    piva: str,
    anno: int,
    trimestre: int,
    logger: Callable[..., None],
) -> List[Dict[str, Any]]:
    """
    Recupera l'elenco dei bolli per un dato anno e trimestre.

    Args:
        session: Sessione requests autenticata
        headers_cons: Headers con token B2B (engine.headers_cons)
        piva: Partita IVA del cliente
        anno: Anno di riferimento (es. 2024)
        trimestre: Trimestre (1, 2, 3, 4)
        logger: Funzione di logging

    Returns:
        Lista di dict con i dati dei bolli disponibili
    """
    if trimestre not in (1, 2, 3, 4):
        logger(f"  Bollo: trimestre {trimestre} non valido (usare 1-4)")
        return []

    logger(f"Recupero elenco bolli per P.IVA {piva} anno {anno} T{trimestre}...")

    url = f"{BOLLO_LIST_ENDPOINT}/X/{anno}/{trimestre}?v={unix_ms()}"

    try:
        r = session.get(url, headers=headers_cons, verify=get_ca_bundle())
    except Exception as e:
        logger(f"  Bollo: errore rete — {e}")
        return []

    if r.status_code != 200:
        logger(f"  Bollo: HTTP {r.status_code} per elenco {anno}/T{trimestre}")
        return []

    try:
        data = r.json()
    except Exception as e:
        logger(f"  Bollo: JSON malformato — {e}")
        return []

    elenco = data.get("fattureBollo") or data.get("elenco") or []
    logger(f"  Bollo: trovati {len(elenco)} elementi")
    return elenco


def fetch_bollo_dettaglio(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    piva: str,
    anno: int,
    trimestre: int,
    logger: Callable[..., None],
) -> Optional[Dict[str, Any]]:
    """
    Recupera il dettaglio di un bollo (opzionale — l'elenco contiene già i dati).

    Args:
        session: Sessione autenticata
        headers_cons: Headers con token B2B
        piva: Partita IVA
        anno: Anno di riferimento
        trimestre: Trimestre (1, 2, 3, 4)
        logger: Funzione di logging

    Returns:
        Dict con il dettaglio oppure None
    """
    logger(f"Recupero dettaglio bollo {piva} {anno} T{trimestre}...")

    url = f"{BOLLO_DETTAGLIO_ENDPOINT}/{trimestre}{anno}{piva}?v={unix_ms()}"

    try:
        r = session.get(url, headers=headers_cons, verify=get_ca_bundle())
    except Exception as e:
        logger(f"  Bollo dettaglio: errore rete — {e}")
        return None

    if r.status_code != 200:
        logger(f"  Bollo dettaglio: HTTP {r.status_code}")
        return None

    try:
        return r.json()
    except Exception:
        logger("  Bollo dettaglio: JSON non valido")
        return None


def fetch_bollo_pdf(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    bollo_data: Dict[str, Any],
    logger: Callable[..., None],
) -> Optional[bytes]:
    """
    Genera e scarica il PDF del modello F24 per un bollo.

    Args:
        session: Sessione autenticata
        headers_cons: Headers con token B2B
        bollo_data: Oggetto bollo dall'elenco (dict con i dati del bollo)
        logger: Funzione di logging

    Returns:
        Contenuto bytes del PDF, oppure None
    """
    logger("Generazione PDF F24 bollo...")

    # Prepara headers per POST con JSON
    post_headers = dict(headers_cons)
    post_headers["Content-Type"] = "application/json"

    try:
        r = session.post(
            BOLLO_PRINT_ENDPOINT,
            data=json.dumps(bollo_data).encode("utf-8"),
            headers=post_headers,
            verify=get_ca_bundle(),
        )
    except Exception as e:
        logger(f"  Bollo PDF: errore rete — {e}")
        return None

    if r.status_code != 200:
        logger(f"  Bollo PDF: HTTP {r.status_code}")
        return None

    # Verifica che sia un PDF
    content_type = r.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and not r.content[:4] == b"%PDF":
        logger(f"  Bollo PDF: Content-Type inaspettato: {content_type}")
        # Potrebbe comunque essere un PDF, proviamo a salvarlo lo stesso

    logger(f"  Bollo PDF: {len(r.content)} bytes ricevuti")
    return r.content


def fetch_bolli_for_year(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    piva: str,
    anno: int,
    logger: Callable[..., None],
    download_pdf: bool = True,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Scarica tutti i bolli per tutti i trimestri di un dato anno.

    Args:
        session: Sessione autenticata
        headers_cons: Headers con token B2B
        piva: Partita IVA del cliente
        anno: Anno di riferimento
        logger: Funzione di logging
        download_pdf: Se True, genera anche i PDF

    Returns:
        Dict {trimestre: [lista bolli con pdf_bytes opzionale]}
    """
    risultati: Dict[int, List[Dict[str, Any]]] = {}

    for trimestre in range(1, 5):
        logger(f"\n--- Trimestre {trimestre}/{anno} ---")
        elenco = fetch_bollo_list(session, headers_cons, piva, anno, trimestre, logger)
        if not elenco:
            logger(f"Nessun bollo per T{trimestre}/{anno}")
            continue

        records: List[Dict[str, Any]] = []
        for item in elenco:
            entry = dict(item)
            entry.setdefault("piva", piva)
            entry.setdefault("anno", anno)
            entry.setdefault("trimestre", trimestre)

            if download_pdf:
                pdf_content = fetch_bollo_pdf(session, headers_cons, item, logger)
                if pdf_content:
                    entry["pdf_bytes"] = pdf_content

            records.append(entry)

        if records:
            risultati[trimestre] = records

    return risultati


# ─── Salvataggio ────────────────────────────────────────────────────────────────


def export_json(
    records_by_trimestre: Dict[int, List[Dict[str, Any]]],
    piva: str,
    anno: int,
    output_dir: str,
) -> str:
    """Salva i metadati dei bolli in JSON (escludendo i pdf_bytes)."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"F24_BOLLI_{piva}_{anno}.json"
    path = os.path.join(output_dir, filename)

    # Prepara i record senza i bytes PDF
    clean_records: List[Dict[str, Any]] = []
    for trimestre, records in sorted(records_by_trimestre.items()):
        for rec in records:
            clean = {k: v for k, v in rec.items() if k != "pdf_bytes"}
            clean_records.append(clean)

    payload = {
        "piva": piva,
        "anno": anno,
        "data_estrazione": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totale": len(clean_records),
        "bolli": clean_records,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path


def save_pdf(
    pdf_content: bytes,
    piva: str,
    anno: int,
    trimestre: int,
    output_dir: str,
    suffix: str = "",
) -> str:
    """Salva un file PDF del modello F24 bollo."""
    os.makedirs(output_dir, exist_ok=True)
    suff = f"_{suffix}" if suffix else ""
    filename = f"F24_BOLLO_{piva}_{anno}_T{trimestre}{suff}.pdf"
    path = os.path.join(output_dir, filename)

    with open(path, "wb") as f:
        f.write(pdf_content)

    return path


# ─── Funzione principale ────────────────────────────────────────────────────────


def run(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    piva: str,
    anno: int,
    logger: Callable[..., None],
    output_root: str = "output",
    fmt: str = "both",
    download_pdf: bool = True,
    client_dir: Optional[str] = None,
) -> bool:
    """
    Esegue il download completo dei modelli F24 bolli per un cliente e anno.

    Args:
        session: Sessione autenticata
        headers_cons: Headers con token B2B
        piva: Partita IVA del cliente (usata per le API)
        anno: Anno di riferimento
        logger: Funzione di logging
        output_root: Directory radice per l'output
        fmt: Formato di output ('json', 'pdf', 'both')
        download_pdf: Se True, genera i PDF
        client_dir: Nome directory personalizzato per il cliente (es. "RIZZO_ANGELA").
                    Se None, usa ``piva``.

    Returns:
        True se completato con successo
    """
    logger(f"\n=== Download F24 BOLLI per P.IVA {piva} anno {anno} ===")

    records_by_trimestre = fetch_bolli_for_year(
        session, headers_cons, piva, anno, logger, download_pdf=download_pdf
    )

    if not records_by_trimestre:
        logger(f"Nessun bollo trovato per {piva} anno {anno}")
        return True

    # Directory di output: output/<CLIENTE>/<ANNO>/f24bolli/
    dir_name = client_dir or piva
    output_dir = os.path.join(output_root, dir_name, str(anno), "f24bolli")

    json_path = None
    if fmt in ("json", "both"):
        json_path = export_json(records_by_trimestre, piva, anno, output_dir)
        logger(f"Bolli: JSON salvato in {json_path}")

    pdf_saved = 0
    if fmt in ("pdf", "both") and download_pdf:
        for trimestre, records in sorted(records_by_trimestre.items()):
            for i, rec in enumerate(records):
                pdf_bytes = rec.get("pdf_bytes")
                if pdf_bytes:
                    suffix = f"{i+1}" if len(records) > 1 else ""
                    pdf_path = save_pdf(
                        pdf_bytes, piva, anno, trimestre, output_dir, suffix=suffix
                    )
                    logger(f"  PDF salvato: {pdf_path}")
                    pdf_saved += 1

    total_records = sum(len(v) for v in records_by_trimestre.values())
    logger(f"Bolli: {total_records} record in {len(records_by_trimestre)} trimestri, "
           f"{pdf_saved} PDF")

    return True


__all__ = [
    "fetch_bollo_list",
    "fetch_bollo_dettaglio",
    "fetch_bollo_pdf",
    "fetch_bolli_for_year",
    "export_json",
    "save_pdf",
    "run",
    "BASE_URL",
    "BOLLO_LIST_ENDPOINT",
    "BOLLO_PRINT_ENDPOINT",
]
