"""
Comando: F24 — Download F24 BOLLI (imposta di bollo) da ivaservizi.

Utilizzo:
    python cli.py f24 bolli --piva XXXXX --anno 2025
    python cli.py f24 bolli --piva XXXXX --anno 2025 --trimestre 1
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

from app.log_config import get_logger

_log = get_logger("cmd.f24")


def add_args(subparsers):
    """Aggiunge il comando 'f24' con sottocomando 'bolli'."""
    p = subparsers.add_parser(
        "f24",
        help="Operazioni F24 (bolli, generici)",
    )
    subs = p.add_subparsers(dest="f24_subcommand", required=True)

    # F24 BOLLI
    pb = subs.add_parser("bolli", help="Download F24 BOLLI (imposta di bollo - ivaservizi)")
    pb.add_argument("--piva", required=True, help="Partita IVA del cliente")
    pb.add_argument("--anno", type=int, required=True, help="Anno di riferimento")
    pb.add_argument("--trimestre", type=int, choices=[1, 2, 3, 4], help="Trimestre (default: tutti)")
    return p


def run(args: Any) -> int:
    """Esegue il comando F24."""
    if args.f24_subcommand == "bolli":
        return _run_bolli(args)
    return 0


def run_bolli_interactive(engine, cfg: Dict[str, str]) -> int:
    """Versione interattiva per F24 bolli."""
    from commands import input_piva

    piva = input_piva()
    if not piva:
        return 0

    anno_raw = input("Anno (es. 2024): ").strip()
    if not anno_raw.isdigit() or len(anno_raw) != 4:
        print("Anno non valido.")
        return 0
    anno = int(anno_raw)

    trimestre_raw = input("Trimestre (1-4, o INVIO per tutti): ").strip()
    trimestre = None
    if trimestre_raw:
        try:
            t = int(trimestre_raw)
            if t in (1, 2, 3, 4):
                trimestre = t
        except ValueError:
            pass

    return _scarica_bolli(engine, piva, anno, trimestre)


def _run_bolli(args: Any) -> int:
    """Esegue download F24 bolli da CLI."""
    from app.config import config
    from app.engine import FEScraperEngine

    config.load()
    engine = FEScraperEngine(_log.info)
    engine.login(
        config.get("CF"),
        config.get("PIN"),
        config.get("PASSWORD"),
    )

    piva = args.piva
    anno = args.anno
    trimestre = args.trimestre

    return _scarica_bolli(engine, piva, anno, trimestre)


def _scarica_bolli(
    engine,
    piva: str,
    anno: int,
    trimestre: Optional[int] = None,
) -> int:
    """Logica comune di download F24 bolli."""
    from app.f24_engine import fetch_bolli_for_year

    if not engine.headers_cons:
        _log.info("Inizializzazione sessione B2B per F24 bolli...")
        engine.select_engine("DELEGA_DIRETTA", "", piva, "FOL")
        engine.get_b2b_tokens()

    if trimestre:
        trimestri = [trimestre]
    else:
        trimestri = [1, 2, 3, 4]

    totale_pdf = 0
    for t in trimestri:
        _log.info("F24 bolli: anno %d trimestre %d...", anno, t)
        bolli = fetch_bolli_for_year(
            session=engine.session,
            headers_cons=engine.headers_cons,
            piva=piva,
            anno=anno,
            logger=_log.info,
            download_pdf=True,
        )
        docs_t = bolli.get(t, [])
        if docs_t:
            for doc in docs_t:
                pdf_bytes = doc.get("pdf_bytes")
                if pdf_bytes:
                    folder = os.path.join("output", piva, str(anno), "f24bolli")
                    os.makedirs(folder, exist_ok=True)
                    fname = f"F24_bollo_{anno}_T{t}_{doc.get('progressivo', '0')}.pdf"
                    fpath = os.path.join(folder, fname)
                    with open(fpath, "wb") as f:
                        f.write(pdf_bytes)
                    totale_pdf += 1
                    _log.info(f"  Salvato: {fpath}")

        _log.info(f"  T{t}: {len(docs_t)} bolli trovati")

    print(f"\nF24 BOLLI: scaricati {totale_pdf} PDF per {piva} anno {anno}.")
    return 0 if totale_pdf > 0 else 1
