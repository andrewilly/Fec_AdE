"""
Comando: Fatture — Download fatture elettroniche (RICEVUTE, EMESSE, TRANSFRONTALIERE).

Utilizzo:
    python cli.py fatture --incaricato
    python cli.py fatture --delega-diretta CF [CF ...]
    python cli.py fatture --delega-diretta --from-csv
    python cli.py fatture --all
    python cli.py fatture --incaricato --anno 2025
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from commands import (
    esegui_deleghe,
    load_clienti_completi,
    load_clienti_delega_diretta,
    load_clienti_incaricato,
    login_engine,
    scegli_cliente_interattivo,
)
from app.log_config import get_logger

_log = get_logger("cmd.fatture")


def add_args(subparsers):
    """Aggiunge il sottocomando 'fatture' al parser principale."""
    p = subparsers.add_parser(
        "fatture",
        help="Download fatture elettroniche per clienti",
        description="Scarica fatture RICEVUTE, EMESSE e (opzionalmente) TRANSFRONTALIERE.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--incaricato", action="store_true",
        help="Carica clienti dal wizard Incaricato",
    )
    g.add_argument(
        "--delega-diretta", nargs="*", metavar="CF",
        help="CF in delega diretta (separati da spazio). Senza argomenti carica da CSV.",
    )
    g.add_argument(
        "--all", "--completo", action="store_true", dest="all",
        help="Elenco completo clienti (incaricato + delega diretta)",
    )
    p.add_argument(
        "--anno", type=int, default=0,
        help="Anno di competenza (default: anno corrente)",
    )
    p.add_argument(
        "--no-corrispettivi", action="store_true",
        help="NON scaricare i corrispettivi (default: scaricati sempre)",
    )
    p.add_argument(
        "--transfrontaliere", action="store_true",
        help="Scarica anche le fatture transfrontaliere",
    )
    return p


def run(args: Any) -> int:
    """Esegue il comando fatture."""
    from datetime import datetime
    from app.config import config
    from app.log_config import set_active_log_file, get_log_file_path
    from app.database import configure_database

    # Carica configurazione
    env_file = getattr(args, "env_file", None)
    config.load(env_file)
    cfg = _build_cfg(config, args)

    log_path = get_log_file_path(env_file or ".env")
    set_active_log_file(log_path)
    configure_database(env_file)

    # Login
    engine = login_engine(cfg["CF"], cfg["PIN"], cfg["PASSWORD"])

    # Carica clienti
    if args.incaricato:
        clienti = load_clienti_incaricato(engine)
        if not clienti:
            print("Nessun cliente Incaricato disponibile.")
            return 1
        print(f"Trovati {len(clienti)} clienti Incaricato.")
    elif args.delega_diretta is not None:
        clienti = load_clienti_delega_diretta(
            cfs=args.delega_diretta if args.delega_diretta else None,
        )
        if not clienti:
            return 1
    elif args.all:
        clienti = load_clienti_completi(engine)
        if not clienti:
            print("Nessun cliente trovato.")
            return 1
    else:
        print("Nessuna modalità selezionata.")
        return 1

    # Se solo 1 cliente, esegui subito; altrimenti mostra menu
    if len(clienti) == 1:
        selezionati = clienti
    else:
        sel = scegli_cliente_interattivo(clienti)
        if not sel:
            return 0
        selezionati = sel

    # Aggiorna cfg con anno se specificato
    if args.anno:
        cfg["DATA_DAL"] = f"01/01/{args.anno}"
        cfg["DATA_AL"] = f"31/12/{args.anno}"
    # Corrispettivi sempre attivi (salvo esplicito --no-corrispettivi)
    cfg["CORRISPETTIVI"] = "0" if getattr(args, "no_corrispettivi", False) else "1"
    if args.transfrontaliere:
        cfg["TRANSFRONTALIERE"] = "1"

    esegui_deleghe(selezionati, cfg, engine)
    return 0


def run_interactive(cfg: Dict[str, str], engine) -> int:
    """Versione interattiva (chiamata da menu.py) — include sempre corrispettivi."""
    cfg = dict(cfg)
    cfg["CORRISPETTIVI"] = "1"
    cfg["TRANSFRONTALIERE"] = "1"

    # Chiedi anno
    from commands import input_anno
    anno = input_anno()
    cfg["DATA_DAL"] = f"01/01/{anno}"
    cfg["DATA_AL"] = f"31/12/{anno}"

    print(f"\n--- DOWNLOAD FATTURE + CORRISPETTIVI anno {anno} ---")
    print("Scegli origine clienti:")
    print("  1. Incaricato (elenco clienti dal wizard)")
    print("  2. Delega Diretta (CF manuali o da file CSV)")
    print("  7. Elenco completo (incaricato + delega diretta)")
    print("  0. TORNA INDIETRO")
    sub = input("\nScelta: ").strip()

    if sub == "0":
        return 0

    if sub == "1":
        clienti = load_clienti_incaricato(engine)
    elif sub == "2":
        raw = input("CF (separati da virgola, o 'tutti' per CSV): ").strip()
        if not raw:
            return 0
        if raw.lower() == "tutti":
            clienti = load_clienti_delega_diretta()
        else:
            cfs = [cf.strip() for cf in raw.replace(";", ",").split(",") if cf.strip()]
            clienti = load_clienti_delega_diretta(cfs)
    elif sub == "7":
        clienti = load_clienti_completi(engine)
        # Mappa tipo_delega → motore per compatibilità con esegui_deleghe
        from app.deleghe_reader import TIPO_DELEGA_INCARICATO, TIPO_DELEGA_DIRETTA
        for c in clienti:
            if "motore" not in c:
                td = c.get("tipo_delega", TIPO_DELEGA_INCARICATO)
                c["motore"] = "INTERMEDIARIO" if td == TIPO_DELEGA_INCARICATO else "DELEGA_DIRETTA"
    else:
        print("Scelta non valida.")
        return 0

    if not clienti:
        return 0

    print(f"\nTrovati {len(clienti)} clienti.")

    # Se richiesta selezione
    if len(clienti) > 1:
        sel = scegli_cliente_interattivo(clienti)
        if not sel:
            return 0
    else:
        sel = clienti

    esegui_deleghe(sel, cfg, engine)
    return 0


def _build_cfg(config_obj, args) -> Dict[str, str]:
    from datetime import datetime
    _default_year = str(datetime.now().year)
    return {
        "CF": config_obj.get("CF"),
        "PIN": config_obj.get("PIN"),
        "PASSWORD": config_obj.get("PASSWORD"),
        "DATA_DAL": config_obj.get("DATA_DAL", f"01/01/{_default_year}"),
        "DATA_AL": config_obj.get("DATA_AL", f"31/12/{_default_year}"),
        "DB": config_obj.get("DB", "0"),
        "WRITE": config_obj.get("WRITE", "1"),
        "DAILY": config_obj.get("DAILY", "0"),
        "CORRISPETTIVI": config_obj.get("CORRISPETTIVI", "0"),
        "TRANSFRONTALIERE": config_obj.get("TRANSFRONTALIERE", "0"),
    }
