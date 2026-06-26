"""
Download F24 generici (tasse, contributi) dal Cassetto Fiscale.

IMPLEMENTAZIONE REALE basata su reverse engineering di RecuFatture.exe.

Il Cassetto Fiscale (cassetto.agenziaentrate.gov.it) è un sistema SEPARATO
da Fatture e Corrispettivi (ivaservizi), ma utilizza la stessa sessione SSO.

Endpoint scoperti:
  - initCassetto  : POST /casshome-rest/rs/initCassetto?v=
  - cambiaCliente : POST /casshome-rest/rs/cambiaCliente?v=
  - CassettoFiscaleServlet : GET /cassfisc-web/CassettoFiscaleServlet?Ric=F24&Anno=

NOTA: Gli F24 bolli (imposta di bollo) sono su ivaservizi e gestiti da
      app/f24_engine.py. Questo modulo gestisce gli F24 generici (tasse,
      contributi previdenziali, IVA, etc.) dal Cassetto Fiscale.
"""

import os
from typing import Any, Callable, Dict, List, Optional

from app.cassetto_fiscale_engine import (
    CassettoFiscaleEngine,
    get_tipi_documento,
    export_json,
)

TIPI_DOCUMENTO = get_tipi_documento()


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
    Esegue il download degli F24 generici per un cliente e anno.

    Args:
        session: Sessione requests autenticata (da FEScraperEngine.login).
        headers_cons: Headers con token B2B (non usati dal Cassetto Fiscale,
                      ma mantenuti per compatibilità API).
        cf: Codice fiscale del cliente.
        anno: Anno di riferimento.
        logger: Funzione di logging.
        output_root: Directory radice per l'output.
        tipo: Non usato per F24 (mantenuto per compatibilità).
        download_pdf: Se True, scarica anche i PDF.
        piva: Partita IVA del cliente (se diversa da cf, es. per intermediari).

    Returns:
        True se completato con successo.
    """
    from app.cassetto_fiscale_engine import run_f24_generici as _run

    # Se piva non specificata, prova a usare cf
    piva_effettiva = piva or cf

    # Inizializza il motore Cassetto Fiscale
    cf_engine = CassettoFiscaleEngine(session, logger)
    cf_engine.init_session()

    return _run(
        engine=cf_engine,
        cf=cf,
        piva=piva_effettiva,
        anno=anno,
        logger=logger,
        output_root=output_root,
        download_pdf=download_pdf,
    )


def probe_cassetto_fiscale_endpoints(
    session: "requests.Session",
    cf: str,
    logger: Callable[..., None],
) -> Dict[str, int]:
    """
    Verifica gli endpoint reali del Cassetto Fiscale.

    Usa la sessione già autenticata (dopo login IAMPE) per testare
    tutti gli endpoint conosciuti del Cassetto Fiscale.

    Args:
        session: Sessione requests autenticata.
        cf: Codice fiscale (per logging, non critico).
        logger: Funzione di logging.

    Returns:
        Dict con URL -> HTTP status code.
    """
    from app.cassetto_fiscale_engine import (
        CASSETTO_REST,
        CASSETTO_SERVLET,
        CASSETTO_HOME,
    )

    risultati: Dict[str, int] = {}

    logger("=== Probe endpoint Cassetto Fiscale ===")

    # Test home page (SSO)
    try:
        r = session.get(CASSETTO_HOME, verify=True, timeout=10)
        risultati[CASSETTO_HOME] = r.status_code
        logger(f"  {r.status_code:3d}  {CASSETTO_HOME}")
    except Exception as e:
        risultati[CASSETTO_HOME] = 0
        logger(f"  ERR  {CASSETTO_HOME} — {e}")

    # Test initLight
    init_light = f"{CASSETTO_REST}/initLight"
    try:
        r = session.get(init_light, verify=True, timeout=10)
        risultati[init_light] = r.status_code
        logger(f"  {r.status_code:3d}  {init_light}")
    except Exception as e:
        risultati[init_light] = 0
        logger(f"  ERR  {init_light} — {e}")

    # Test initCassetto
    for endpoint in [
        f"{CASSETTO_REST}/initCassetto",
        f"{CASSETTO_REST}/initCassetto?v=",
    ]:
        try:
            r = session.get(endpoint, verify=True, timeout=10)
            risultati[endpoint] = r.status_code
            logger(f"  {r.status_code:3d}  {endpoint}")
        except Exception as e:
            risultati[endpoint] = 0
            logger(f"  ERR  {endpoint} — {e}")

    # Test servlet con vari tipi
    for ric in ["F24", "DetF24", "RED", "730", "770"]:
        url = f"{CASSETTO_SERVLET}?Ric={ric}&Anno=2024"
        try:
            r = session.get(url, verify=True, timeout=10)
            risultati[url] = r.status_code
            logger(f"  {r.status_code:3d}  {url}")
        except Exception as e:
            risultati[url] = 0
            logger(f"  ERR  {url} — {e}")

    return risultati


__all__ = [
    "run",
    "TIPI_DOCUMENTO",
    "probe_cassetto_fiscale_endpoints",
]
