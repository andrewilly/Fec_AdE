"""
Comando: Cassetto Fiscale — Accesso al Cassetto Fiscale dell'Agenzia delle Entrate.

Gestisce F24 generici, Certificazioni Uniche (CU), dichiarazioni dei redditi.

NOTA: Il Cassetto Fiscale richiede JavaScript per inizializzare la sessione.
      Il modulo tenta prima l'engine request-based; se fallisce con HTTP 409
      persistente, passa automaticamente al browser Playwright (se installato).

Utilizzo:
    python cli.py cassetto f24-generici --piva CF --anno 2025
    python cli.py cassetto cu --piva CF --anno 2025
    python cli.py cassetto dichiarazioni --piva CF --anno 2025 --tipo RED
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.log_config import get_logger
from app.engine import FEScraperEngine

_log = get_logger("cmd.cassetto")

# ─── Type alias per engine unificato ─────────────────────────────────────────

_CassettoEngine = Any  # CassettoFiscaleEngine | CassettoFiscaleBrowserEngine


# ═══════════════════════════════════════════════════════════════════════════════
# Engine unificato con fallback automatico browser
# ═══════════════════════════════════════════════════════════════════════════════


def _init_cassetto_engine(
    engine: FEScraperEngine,
    piva: str,
    logger_func: Callable[..., None],
    cf: str = "",
    pin: str = "",
    password: str = "",
) -> _CassettoEngine:
    """
    Inizializza un engine Cassetto Fiscale con fallback a Playwright.

    Prova prima l'engine request-based (CassettoFiscaleEngine).
    Se init_session() restituisce 409 persistente, passa al browser
    (CassettoFiscaleBrowserEngine) se Playwright + Chromium è disponibile.

    Args:
        engine: FEScraperEngine già autenticato.
        piva: CF/P.IVA del cliente target.
        logger_func: Funzione di logging.
        cf, pin, password: Credenziali per il login browser (fallback).

    Returns:
        Un engine Cassetto Fiscale inizializzato (request o browser).
    """
    from app.cassetto_fiscale_engine import CassettoFiscaleEngine

    # Tentativo 1: engine request-based
    cf_engine = CassettoFiscaleEngine(engine.session, logger_func)
    try:
        cf_engine.init_session()
    except Exception as e:
        logger_func(f"  Engine request: init fallito ({e}) — provo browser...")
    else:
        if cf_engine.initialized:
            # init_session è riuscita (200 o 409 considerato OK dal codice)
            try:
                _try_cambia_cliente_se_needed(cf_engine, piva, logger_func)
                return cf_engine
            except Exception:
                # Cambio cliente fallito — proviamo col browser se disponibile
                logger_func("  Cambio cliente fallito via request — provo browser...")
                pass

    # Tentativo 2: browser engine (Playwright)
    return _init_browser_engine(engine, piva, logger_func, cf, pin, password)


def _try_cambia_cliente_se_needed(
    cf_engine: _CassettoEngine,
    piva: str,
    logger_func: Callable[..., None],
):
    """Cambia cliente se l'engine supporta il metodo cambia_cliente."""
    if hasattr(cf_engine, "cambia_cliente") and callable(cf_engine.cambia_cliente):
        try:
            cf_engine.cambia_cliente(piva)
        except Exception:
            raise
    # Se non ha cambia_cliente (es. browser engine), assume già attivo


def _init_browser_engine(
    engine: FEScraperEngine,
    piva: str,
    logger_func: Callable[..., None],
    cf: str,
    pin: str,
    password: str,
) -> _CassettoEngine:
    """Inizializza il Cassetto Fiscale via Playwright browser."""
    from app.cassetto_fiscale_browser import (
        CassettoFiscaleBrowserEngine,
        PLAYWRIGHT_AVAILABLE,
    )

    if not PLAYWRIGHT_AVAILABLE:
        logger_func("Playwright non installato.")
        logger_func("Per il Cassetto Fiscale serve un browser. Installa:")
        logger_func("  pip install playwright")
        logger_func("  python -m playwright install chromium")
        raise RuntimeError(
            "Cassetto Fiscale non accessibile via request HTTP (HTTP 409). "
            "Installa Playwright e Chromium per il fallback browser."
        )

    if not all([cf, pin, password]):
        raise RuntimeError(
            "Credenziali mancanti per il login browser del Cassetto Fiscale."
        )

    logger_func("Avvio Cassetto Fiscale via browser (Playwright)...")
    be = CassettoFiscaleBrowserEngine(logger_func, headless=True)
    be.init_session(cf, pin, password)

    # Cambio cliente solo per intermediari (engine != 'ME_STESSO')
    try:
        be.cambia_cliente(piva)
    except Exception as e:
        # Se il cambio cliente fallisce, potrebbe essere 'me stesso'
        logger_func(f"  Cambio cliente browser: {e} — procedo come 'me stesso'.")

    return be


# ═══════════════════════════════════════════════════════════════════════════════
# Utility cartelle
# ═══════════════════════════════════════════════════════════════════════════════


def _client_dir_name(cf_piva: str) -> str:
    """Determina nome cartella output per cliente (con ragione sociale se nota)."""
    from app.deleghe_reader import RAGIONI_SOCIALI_FILE
    import json
    if os.path.exists(RAGIONI_SOCIALI_FILE):
        try:
            with open(RAGIONI_SOCIALI_FILE, "r", encoding="utf-8") as f:
                mappa = json.load(f)
            rs = mappa.get(cf_piva, "")
            if rs:
                val = rs.strip().upper()
                for c in r'\/:*?"<>|':
                    val = val.replace(c, "_")
                val = val.replace(" ", "_")
                return val
        except Exception:
            pass
    return cf_piva


def _salva_documenti(
    records: List[Dict[str, Any]],
    cf_engine: _CassettoEngine,
    piva: str,
    anno: int,
    output_root: str,
    prefix: str,
    tipo_etichetta: str,
    cartella: str,
    logger_func: Callable[..., None],
) -> bool:
    """Salva sul disco i documenti ottenuti dal Cassetto Fiscale."""
    if not records:
        logger_func(f"Nessun documento {tipo_etichetta} per {piva} anno {anno}")
        return False

    client_dir = _client_dir_name(piva)
    base_output = os.path.join(output_root, client_dir, str(anno), cartella)
    os.makedirs(base_output, exist_ok=True)

    pdf_dir = os.path.join(base_output, "PDF")
    scaricati = 0

    for idx, doc in enumerate(records, 1):
        url = doc.get("url") or doc.get("href") or ""
        local_path = doc.get("local_path") or ""
        if not url and not local_path:
            continue

        data = doc.get("data", "")
        protocollo = doc.get("protocollo", "")
        if data and protocollo:
            prot_clean = protocollo.replace("/", "_").replace("\\", "_")
            prot_clean = re.sub(r'[\\/*?:"<>|]', "_", prot_clean)
            fname = f"{prefix}_{data.replace('/', '-')}_{prot_clean}.pdf"
        else:
            fname = f"{prefix}_{anno}_{idx:02d}.pdf"

        # Se già scaricato durante la discovery (es. CU via form POST)
        if local_path and os.path.exists(local_path):
            os.makedirs(pdf_dir, exist_ok=True)
            shutil.copy2(local_path, os.path.join(pdf_dir, fname))
            scaricati += 1
            continue

        # Download via engine
        fpath = cf_engine.download_document(url, pdf_dir, filename=fname)
        if fpath:
            scaricati += 1

    logger_func(f"{tipo_etichetta}: scaricati {scaricati}/{len(records)} per {piva} anno {anno}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# F24 Generici
# ═══════════════════════════════════════════════════════════════════════════════


def run_f24_generici(
    engine: FEScraperEngine,
    piva: str,
    anno: int,
    logger_func: Callable[..., None],
    output_root: str = "output",
    cf: str = "",
    pin: str = "",
    password: str = "",
) -> bool:
    """Scarica F24 generici (tasse/contributi) dal Cassetto Fiscale."""
    logger_func(f"Download F24 generici per {piva} anno {anno}...")

    cf_engine = _init_cassetto_engine(engine, piva, logger_func, cf, pin, password)
    records = cf_engine.fetch_document_list("F24", anno, piva=piva)

    return _salva_documenti(
        records, cf_engine, piva, anno, output_root,
        prefix="F24", tipo_etichetta="F24 generici",
        cartella="f24generici", logger_func=logger_func,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Certificazioni Uniche (CU)
# ═══════════════════════════════════════════════════════════════════════════════


def run_cu(
    engine: FEScraperEngine,
    piva: str,
    anno: int,
    logger_func: Callable[..., None],
    output_root: str = "output",
    cf: str = "",
    pin: str = "",
    password: str = "",
) -> bool:
    """Scarica Certificazioni Uniche (CU) dal Cassetto Fiscale."""
    logger_func(f"Download CU per {piva} anno {anno}...")

    cf_engine = _init_cassetto_engine(engine, piva, logger_func, cf, pin, password)
    records = cf_engine.fetch_document_list("CUK", anno, piva=piva)

    return _salva_documenti(
        records, cf_engine, piva, anno, output_root,
        prefix="CU", tipo_etichetta="CU",
        cartella="certificazioniuniche", logger_func=logger_func,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dichiarazioni (RED, 730, 770, IVA, IRAP, Unico)
# ═══════════════════════════════════════════════════════════════════════════════


def run_dichiarazioni(
    engine: FEScraperEngine,
    piva: str,
    anno: int,
    logger_func: Callable[..., None],
    tipo: str = "RED",
    output_root: str = "output",
    cf: str = "",
    pin: str = "",
    password: str = "",
) -> bool:
    """Scarica dichiarazioni dei redditi dal Cassetto Fiscale."""
    from app.cassetto_fiscale_engine import TIPI_DOCUMENTO, TIPO_TO_RIC

    ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())
    if ric not in TIPI_DOCUMENTO:
        logger_func(f"Tipo '{tipo}' non riconosciuto. Tipi: {', '.join(sorted(TIPI_DOCUMENTO.keys()))}")
        return False

    logger_func(f"Download dichiarazioni {ric} per {piva} anno {anno}...")

    cf_engine = _init_cassetto_engine(engine, piva, logger_func, cf, pin, password)
    records = cf_engine.fetch_document_list(ric, anno, piva=piva)

    return _salva_documenti(
        records, cf_engine, piva, anno, output_root,
        prefix=ric, tipo_etichetta=f"Dichiarazioni {ric}",
        cartella=f"dichiarazioni_{ric.lower()}", logger_func=logger_func,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI (argparse)
# ═══════════════════════════════════════════════════════════════════════════════


def add_args(subparsers):
    """Aggiunge il comando 'cassetto' con sottocomandi."""
    p = subparsers.add_parser(
        "cassetto",
        help="Operazioni Cassetto Fiscale (F24 generici, CU, dichiarazioni)",
    )
    subs = p.add_subparsers(dest="cassetto_subcommand", required=True)

    pf = subs.add_parser("f24-generici", help="Download F24 generici (tasse/contributi)")
    pf.add_argument("--piva", required=True, help="CF / P.IVA del cliente")
    pf.add_argument("--anno", type=int, required=True, help="Anno di riferimento")

    pc = subs.add_parser("cu", help="Download Certificazioni Uniche")
    pc.add_argument("--piva", required=True, help="CF / P.IVA del cliente")
    pc.add_argument("--anno", type=int, required=True, help="Anno di riferimento")

    pd = subs.add_parser("dichiarazioni", help="Download dichiarazioni dei redditi")
    pd.add_argument("--piva", required=True, help="CF / P.IVA del cliente")
    pd.add_argument("--anno", type=int, required=True, help="Anno di riferimento")
    pd.add_argument("--tipo", default="RED",
                    help="Tipo: RED, 730, 770, IVA, IRA, UNI (default: RED)")
    return p


def run(args: Any) -> int:
    """Esegue il comando cassetto da CLI."""
    from app.config import config

    config.load()
    cf = config.get("CF")
    pin = config.get("PIN")
    password = config.get("PASSWORD")

    engine = FEScraperEngine(_log.info)
    engine.login(cf, pin, password)

    sub = args.cassetto_subcommand
    piva = args.piva
    anno = args.anno

    kwargs = {"cf": cf, "pin": pin, "password": password}

    if sub == "f24-generici":
        ok = run_f24_generici(engine, piva, anno, _log.info, **kwargs)
    elif sub == "cu":
        ok = run_cu(engine, piva, anno, _log.info, **kwargs)
    elif sub == "dichiarazioni":
        ok = run_dichiarazioni(engine, piva, anno, _log.info, tipo=args.tipo, **kwargs)
    else:
        print("Sottocomando sconosciuto.")
        return 1

    return 0 if ok else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Versioni interattive (per menu.py)
# ═══════════════════════════════════════════════════════════════════════════════


def run_f24_generici_interactive(engine, cfg) -> int:
    """Versione interattiva per F24 generici."""
    from commands import input_piva, input_anno

    piva = input_piva()
    if not piva:
        return 0
    anno = input_anno()

    creds = {"cf": cfg.get("CF", ""), "pin": cfg.get("PIN", ""), "password": cfg.get("PASSWORD", "")}
    ok = run_f24_generici(engine, piva, anno, _log.info, **creds)
    return 0 if ok else 1


def run_cu_interactive(engine, cfg) -> int:
    """Versione interattiva per CU."""
    from commands import input_piva, input_anno

    piva = input_piva()
    if not piva:
        return 0
    anno = input_anno()

    creds = {"cf": cfg.get("CF", ""), "pin": cfg.get("PIN", ""), "password": cfg.get("PASSWORD", "")}
    ok = run_cu(engine, piva, anno, _log.info, **creds)
    return 0 if ok else 1


def run_dichiarazioni_interactive(engine, cfg) -> int:
    """Versione interattiva per dichiarazioni."""
    from commands import input_piva, input_anno
    from app.cassetto_fiscale_engine import TIPI_DOCUMENTO

    piva = input_piva()
    if not piva:
        return 0
    anno = input_anno()

    print("\nTipi dichiarazione disponibili:")
    for k, v in sorted(TIPI_DOCUMENTO.items()):
        print(f"  {k:6s} — {v}")
    tipo = input("\nTipo (INVIO = RED): ").strip() or "RED"

    creds = {"cf": cfg.get("CF", ""), "pin": cfg.get("PIN", ""), "password": cfg.get("PASSWORD", "")}
    ok = run_dichiarazioni(engine, piva, anno, _log.info, tipo=tipo, **creds)
    return 0 if ok else 1
