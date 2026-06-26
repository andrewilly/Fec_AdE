"""
Comando: Tutto — Scarica tutto (fatture + corrispettivi + transfrontaliere)
per uno o più clienti in un dato anno.

Utilizzo:
    python cli.py tutto --cliente CF --anno 2025
    python cli.py tutto --all --anno 2025
    python cli.py tutto --incaricato --anno 2025
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.log_config import get_logger
from app.engine import FEScraperEngine
from app.runner import run_pipeline, PipelineConfig

_log = get_logger("cmd.tutto")


def add_args(subparsers):
    """Aggiunge il comando 'tutto'."""
    p = subparsers.add_parser(
        "tutto",
        help="Scarica TUTTO per cliente e anno (fatture, corrispettivi, transfrontaliere)",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--cliente", metavar="CF", help="CF/P.IVA del singolo cliente")
    src.add_argument("--incaricato", action="store_true", help="Carica da Incaricato e seleziona")
    src.add_argument("--all", action="store_true", help="Tutti i clienti (incaricato + delega diretta)")
    p.add_argument("--anno", type=int, default=0, help="Anno di competenza")
    return p


def run(args: Any) -> int:
    """Esegue il comando tutto da CLI."""
    from app.config import config
    from commands import login_engine
    from commands.fatture import _build_cfg as build_fatture_cfg

    config.load()
    cfg = build_fatture_cfg(config, args)
    engine = login_engine(cfg["CF"], cfg["PIN"], cfg["PASSWORD"])

    anno = args.anno or datetime.now().year

    if args.cliente:
        return _scarica_per_cliente(engine, cfg, args.cliente, anno)
    elif args.incaricato:
        from commands import load_clienti_incaricato, scegli_cliente_interattivo
        clienti = load_clienti_incaricato(engine)
        if not clienti:
            print("Nessun cliente Incaricato disponibile.")
            return 1
        sel = scegli_cliente_interattivo(clienti)
        if not sel:
            return 0
        for cli in sel:
            _scarica_per_cliente(engine, cfg, cli["cf"], anno)
        return 0
    elif args.all:
        from commands import load_clienti_completi
        clienti = load_clienti_completi(engine)
        if not clienti:
            print("Nessun cliente trovato.")
            return 1
        for cli in clienti:
            _scarica_per_cliente(engine, cfg, cli["cf"], anno)
        return 0
    else:
        return run_interactive(engine, cfg)


def _scarica_per_cliente(
    engine: FEScraperEngine,
    cfg: Dict[str, str],
    cf_cliente: str,
    anno: int,
) -> int:
    """Scarica TUTTO per un singolo cliente/anno."""
    data_dal = f"01/01/{anno}"
    data_al = f"31/12/{anno}"

    _log.info(f"Download COMPLETO per {cf_cliente} anno {anno}")

    pipe_cfg = PipelineConfig(
        cf=cfg["CF"],
        pin=cfg["PIN"],
        password=cfg["PASSWORD"],
        piva=cf_cliente,
        data_dal=data_dal,
        data_al=data_al,
        motore="INTERMEDIARIO",
        tipo="FOL",
        daily=False,
        db_enabled=cfg.get("DB", "0") == "1",
        write=cfg.get("WRITE", "1") != "0",
        corrispettivi=True,
        transfrontaliere=True,
    )

    success = run_pipeline(pipe_cfg, _log.info, engine=engine)
    if success:
        _estrai_e_salva_ragione_sociale(pipe_cfg.piva, cf_cliente)
        _rinomina_cartelle(pipe_cfg.piva)

    return 0 if success else 1


def _estrai_e_salva_ragione_sociale(piva: str, cf: str):
    from commands import _estrai_e_salva_ragione_sociale as _fn
    _fn(piva, cf)


def _rinomina_cartelle(piva: str):
    from commands import _rinomina_cartelle as _fn
    _fn(piva)


def run_interactive(engine: FEScraperEngine, cfg: Dict[str, str]) -> int:
    """Versione interattiva (chiamata da menu.py)."""
    from commands import input_anno, load_clienti_incaricato, load_clienti_completi, load_clienti_delega_diretta, scegli_cliente_interattivo

    print("\n--- SCARICA TUTTO PER CLIENTE E ANNO ---")
    print()
    print("Scegli l'origine dei clienti:")
    print("  1. Incaricato (elenco clienti dal wizard)")
    print("  2. Delega Diretta (CF manuali o da file CSV)")
    print("  7. Elenco completo (incaricato + delega diretta)")
    print("  0. TORNA AL MENU PRINCIPALE")
    sub = input("\nScelta: ").strip()

    if sub == "0":
        return 0

    clienti: List[Dict] = []
    if sub == "1":
        _log.info("Caricamento clienti Incaricato...")
        clienti = load_clienti_incaricato(engine)
        if not clienti:
            print("Nessun cliente Incaricato disponibile.")
            return 0
    elif sub == "2":
        _log.info("Caricamento clienti Delega Diretta...")
        raw = input("CF (separati da virgola, o 'tutti' per CSV): ").strip()
        if not raw:
            return 0
        if raw.lower() == "tutti":
            clienti = load_clienti_delega_diretta()
        else:
            cfs = [cf.strip() for cf in raw.replace(";", ",").split(",") if cf.strip()]
            clienti = load_clienti_delega_diretta(cfs)
    elif sub == "7":
        _log.info("Caricamento elenco completo clienti...")
        clienti = load_clienti_completi(engine)
        if not clienti:
            print("Nessun cliente trovato.")
            return 0
    else:
        print("Scelta non valida.")
        return 0

    if not clienti:
        return 0

    anno = input_anno()

    if len(clienti) > 1:
        sel = scegli_cliente_interattivo(clienti)
        if not sel:
            return 0
    else:
        sel = clienti

    for cli in sel:
        _scarica_per_cliente(engine, cfg, cli["cf"], anno)

    return 0
