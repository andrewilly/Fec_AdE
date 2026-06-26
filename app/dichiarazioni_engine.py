"""
Download delle dichiarazioni dei redditi dal Cassetto Fiscale.

IMPLEMENTAZIONE REALE basata su reverse engineering di RecuFatture.exe.

Utilizza il Cassetto Fiscale (cassetto.agenziaentrate.gov.it) per accedere
a dichiarazioni dei redditi, Modello 730, 770, IVA, IRAP, Unico, etc.

Endpoint scoperti:
  - initCassetto     : POST /casshome-rest/rs/initCassetto?v=
  - cambiaCliente    : POST /casshome-rest/rs/cambiaCliente?v=
  - CassettoFiscaleServlet : GET /cassfisc-web/CassettoFiscaleServlet
        ?Ric=RED&Anno=     (Modello Redditi)
        ?Ric=730&Anno=     (Modello 730)
        ?Ric=770&Anno=     (Modello 770)
        ?Ric=UNI&Anno=     (Unico)
        ?Ric=IVA&Anno=     (Dichiarazione IVA)
        ?Ric=IRA&Anno=     (IRAP)

NOTA: Le API ipotizzate su ivaservizi (usate nella versione placeholder)
      NON sono corrette. Tutte le dichiarazioni passano dal Cassetto Fiscale.
"""

import json
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.cassetto_fiscale_engine import (
    CassettoFiscaleEngine,
    TIPI_DOCUMENTO,
    TIPO_TO_RIC,
    get_tipi_documento,
)

# ─── Costanti ───────────────────────────────────────────────────────────────────

# Mantenute per retrocompatibilità, ma non più usate per gli endpoint reali
BASE_URL_DEPRECATED = "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs"
DICHIARAZIONI_ENDPOINT_DEPRECATED = BASE_URL_DEPRECATED + "/dichiarazioni"

# Tipi di dichiarazione supportati (mappa nome → label)
TIPI_DICHIARAZIONE: Dict[str, str] = {
    k: v for k, v in TIPI_DOCUMENTO.items()
    if k in ("RED", "730", "770", "UNI", "IVA", "IRA")
}


# ─── Funzioni principali ────────────────────────────────────────────────────────


def fetch_dichiarazioni_list(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    cf: str,
    anno: int,
    logger: Callable[..., None],
    tipo: Optional[str] = None,
    piva: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Recupera l'elenco delle dichiarazioni per un cliente in un dato anno
    dal Cassetto Fiscale.

    Args:
        session: Sessione requests autenticata (da FEScraperEngine.login).
        headers_cons: Headers con token B2B (non usati dal Cassetto Fiscale,
                      ma mantenuti per compatibilità API).
        cf: Codice fiscale del cliente.
        anno: Anno di riferimento (es. 2024).
        logger: Funzione di logging.
        tipo: Tipo di dichiarazione (es. 'RED', '730', '770', 'IVA', None=tutti).
        piva: Partita IVA del cliente (se diversa da cf, es. intermediari).

    Returns:
        Lista di dict con i metadati delle dichiarazioni disponibili.
    """
    # Se piva non specificata, usa cf
    piva_effettiva = piva or cf

    # Inizializza il Cassetto Fiscale
    cf_engine = CassettoFiscaleEngine(session, logger)
    cf_engine.init_session()

    # Se tipo non specificato, prova RED (il più comune)
    if tipo:
        ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())
        if ric not in TIPI_DOCUMENTO:
            logger(f"  Tipo dichiarazione '{tipo}' non riconosciuto. Uso 'RED'.")
            ric = "RED"
    else:
        ric = "RED"

    logger(f"Recupero dichiarazioni per {cf} anno {anno} (Ric={ric})...")

    try:
        records = cf_engine.fetch_dichiarazioni_list(anno, tipo=ric, piva=piva_effettiva)
    except Exception as e:
        logger(f"  Dichiarazioni: errore — {e}")
        return []

    # Normalizza i campi per retrocompatibilità
    normalized = []
    for rec in records:
        normalized.append({
            "id": rec.get("url") or rec.get("href") or "",
            "identificativo": rec.get("descrizione") or rec.get("oggetto") or "",
            "tipo": ric,
            "tipo_label": TIPI_DOCUMENTO.get(ric, ric),
            "anno": anno,
            "cf": cf,
            "data": rec.get("data") or rec.get("Data") or "",
            "importo": rec.get("importo") or rec.get("Importo") or "",
            "stato": rec.get("stato") or rec.get("Stato") or "",
            "_raw": dict(rec),
        })

    return normalized


def fetch_dichiarazione_pdf(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    cf: str,
    anno: int,
    identificativo: str,
    logger: Callable[..., None],
    piva: Optional[str] = None,
) -> Optional[bytes]:
    """
    Scarica il file PDF di una singola dichiarazione dal Cassetto Fiscale.

    Args:
        session: Sessione autenticata.
        headers_cons: Headers con token B2B (non usati, per compatibilità).
        cf: Codice fiscale.
        anno: Anno di riferimento (non usato se identificativo è un URL).
        identificativo: URL del documento o identificativo.
        logger: Funzione di logging.
        piva: P.IVA del cliente.

    Returns:
        Contenuto bytes del PDF, oppure None.
    """
    from app.security import get_ca_bundle

    # Se identificativo è già un URL, usalo direttamente
    if identificativo.startswith("http"):
        url = identificativo
    else:
        logger(f"  Attenzione: identificativo '{identificativo}' non è un URL.")
        logger(f"  Usa fetch_dichiarazioni_list() per ottenere URL validi.")
        return None

    logger(f"Download dichiarazione: {url}")

    try:
        r = session.get(url, verify=get_ca_bundle(), timeout=60)
    except Exception as e:
        logger(f"  Download PDF fallito: {e}")
        return None

    if r.status_code != 200:
        logger(f"  Download PDF: HTTP {r.status_code}")
        return None

    return r.content


# ─── Salvataggio ────────────────────────────────────────────────────────────────


def export_json(
    records: List[Dict[str, Any]],
    cf: str,
    anno: int,
    output_dir: str,
) -> str:
    """Salva i metadati delle dichiarazioni in JSON."""
    from app.cassetto_fiscale_engine import export_json as _export

    return _export(records, cf, anno, "DICHIARAZIONI", output_dir)


def save_pdf(
    content: bytes,
    cf: str,
    anno: int,
    identificativo: str,
    output_dir: str,
) -> str:
    """Salva un file PDF della dichiarazione."""
    os.makedirs(output_dir, exist_ok=True)
    # Pulisci il nome
    safe_name = identificativo.replace("/", "_").replace(":", "_").replace("?", "_")
    if len(safe_name) > 100:
        safe_name = safe_name[-100:]
    filename = f"{safe_name}.pdf"
    path = os.path.join(output_dir, filename)

    with open(path, "wb") as f:
        f.write(content)

    return path


# ─── Funzione principale ────────────────────────────────────────────────────────


def run(
    session: "requests.Session",
    headers_cons: Dict[str, str],
    cf: str,
    anno: int,
    logger: Callable[..., None],
    output_root: str = "output",
    tipo: Optional[str] = None,
    download_pdf: bool = True,
    piva: Optional[str] = None,
) -> bool:
    """
    Esegue il download completo delle dichiarazioni per un cliente e anno
    dal Cassetto Fiscale.

    Args:
        session: Sessione autenticata (da FEScraperEngine.login).
        headers_cons: Headers con token B2B (non usati, per compatibilità).
        cf: Codice fiscale del cliente.
        anno: Anno di riferimento.
        logger: Funzione di logging.
        output_root: Directory radice per l'output.
        tipo: Tipo dichiarazione (None = "RED" = Modello Redditi).
        download_pdf: Se True, scarica anche i file PDF.
        piva: P.IVA del cliente (se diversa da cf).

    Returns:
        True se completato con successo.
    """
    piva_effettiva = piva or cf

    from app.cassetto_fiscale_engine import run_dichiarazioni as _run

    # Inizializza il motore Cassetto Fiscale
    cf_engine = CassettoFiscaleEngine(session, logger)
    cf_engine.init_session()

    # Determina il tipo Ric
    if tipo:
        ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())
    else:
        ric = "RED"

    return _run(
        engine=cf_engine,
        cf=cf,
        piva=piva_effettiva,
        anno=anno,
        logger=logger,
        output_root=output_root,
        tipo=ric,
        download_pdf=download_pdf,
    )


# ─── Wrapper per menu.py ────────────────────────────────────────────────────────


def get_tipi_dichiarazione() -> Dict[str, str]:
    """Restituisce i tipi di dichiarazione supportati."""
    return dict(TIPI_DICHIARAZIONE)


__all__ = [
    "fetch_dichiarazioni_list",
    "fetch_dichiarazione_pdf",
    "export_json",
    "save_pdf",
    "run",
    "get_tipi_dichiarazione",
    "TIPI_DICHIARAZIONE",
]
