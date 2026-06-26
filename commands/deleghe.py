"""
Comando: Deleghe — Estrazione deleghe e report scadenze.

Utilizzo:
    python cli.py deleghe estrai
    python cli.py deleghe report
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.log_config import get_logger
from app.deleghe_reader import (
    TIPO_DELEGA_INCARICATO,
    TIPO_DELEGA_DIRETTA,
    fetch_all_deleghe_enhanced,
)

_log = get_logger("cmd.deleghe")


def add_args(subparsers):
    """Aggiunge il comando 'deleghe' con sottocomandi."""
    p = subparsers.add_parser("deleghe", help="Operazioni sulle deleghe")
    subs = p.add_subparsers(dest="deleghe_subcommand", required=True)

    subs.add_parser("estrai", help="Estrai elenco deleghe attive in JSON")
    subs.add_parser("report", help="Report scadenze deleghe")
    return p


def run(args: Any) -> int:
    """Esegue il comando deleghe da CLI."""
    from app.config import config
    from app.engine import FEScraperEngine
    from commands import login_engine

    config.load()
    engine = login_engine(
        config.get("CF"),
        config.get("PIN"),
        config.get("PASSWORD"),
    )
    cf_utente = config.get("CF")

    if args.deleghe_subcommand == "estrai":
        return _estrai(engine, cf_utente)
    elif args.deleghe_subcommand == "report":
        return _report(engine, cf_utente)
    return 0


def run_estrai_interactive(engine, cfg) -> int:
    """Versione interattiva per estrazione deleghe."""
    return _estrai(engine, cfg.get("CF", ""))


def run_report_interactive(engine, cfg) -> int:
    """Versione interattiva per report scadenze."""
    return _report(engine, cfg.get("CF", ""))


def _estrai(engine, cf_utente: str) -> int:
    """Estrae deleghe attive in JSON."""
    print("\n--- Estrazione deleghe in JSON (completo) ---")

    clienti, stat = fetch_all_deleghe_enhanced(
        engine=engine,
        logger_func=_log.info,
    )

    if not clienti:
        print("Nessuna delega attiva trovata.")
        return 1

    data_estr = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_file = datetime.now().strftime("%Y%m%d_%H%M%S")

    deleghe_out = []
    for cli in clienti:
        deleghe_out.append({
            "cf": cli.get("cf", ""),
            "piva": cli.get("piva", ""),
            "ragione_sociale": cli.get("ragione_sociale", ""),
            "tipo": cli.get("tipo", "FOL"),
            "sede": cli.get("sede", ""),
            "tipo_delega": cli.get("tipo_delega", TIPO_DELEGA_INCARICATO),
            "data_fine": cli.get("data_fine", ""),
        })

    payload = {
        "data_estrazione": data_estr,
        "cf_utente": cf_utente,
        "totale": len(deleghe_out),
        "deleghe": deleghe_out,
    }

    os.makedirs("output", exist_ok=True)
    filename = f"deleghe_attive_{cf_utente}_{data_file}.json"
    path = os.path.join("output", filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nEstratte {len(deleghe_out)} deleghe attive (di cui "
          f"{stat.get(TIPO_DELEGA_INCARICATO, 0)} Incaricato, "
          f"{stat.get(TIPO_DELEGA_DIRETTA, 0)} Delega Diretta)")
    print(f"Salvate in: {path}")

    if stat.get(TIPO_DELEGA_DIRETTA, 0) == 0:
        print("\nNOTA: Nessuna delega diretta trovata via API.")
        print("Se hai deleghe dirette, usa ~/.fec_ade/deleghe.csv (esportato dall'area riservata AE).")

    return 0


def _report(engine, cf_utente: str) -> int:
    """Genera report scadenze deleghe."""
    print("\n--- Report scadenze deleghe ---")

    clienti, stat = fetch_all_deleghe_enhanced(
        engine=engine,
        logger_func=_log.info,
    )

    if not clienti:
        print("Nessuna delega attiva trovata.")
        return 1

    oggi = datetime.now().date()
    tra_30 = oggi.replace(day=min(oggi.day, 28))
    try:
        tra_30 = tra_30.replace(month=tra_30.month + 1)
    except ValueError:
        tra_30 = tra_30.replace(month=12, year=tra_30.year + 1)
    # Ripristina giorno se cambiato
    if tra_30.day != oggi.day:
        tra_30 = tra_30.replace(day=min(oggi.day, 28))

    in_scadenza = []
    scadute = []
    valide = 0

    for cli in clienti:
        data_fine_str = cli.get("data_fine", "")
        if not data_fine_str:
            valide += 1
            continue
        try:
            data_fine = datetime.strptime(data_fine_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            valide += 1
            continue

        if data_fine < oggi:
            scadute.append((cli, data_fine))
        elif data_fine <= tra_30:
            in_scadenza.append((cli, data_fine))
        else:
            valide += 1

    print(f"\nTotale deleghe: {len(clienti)}")
    print(f"  Valide:       {valide}")
    print(f"  In scadenza:  {len(in_scadenza)}")
    print(f"  Scadute:      {len(scadute)}")

    if in_scadenza:
        print(f"\n--- Deleghe in scadenza entro 30 giorni (entro {tra_30}) ---")
        for cli, scad in sorted(in_scadenza, key=lambda x: x[1]):
            rs = cli.get("ragione_sociale", "") or ""
            label = f" ({rs})" if rs else ""
            print(f"  {cli['cf']}{label:30s}  scadenza: {scad}")

    if scadute:
        print(f"\n--- Deleghe SCADUTE ---")
        for cli, scad in sorted(scadute, key=lambda x: x[1]):
            rs = cli.get("ragione_sociale", "") or ""
            label = f" ({rs})" if rs else ""
            print(f"  {cli['cf']}{label:30s}  scaduta il: {scad}")

    return 0
