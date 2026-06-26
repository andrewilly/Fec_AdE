#!/usr/bin/env python3
"""
Menu interattivo Fec_AdE — Wrapper che chiama i comandi di commands/*.

Utilizzo:
    python menu.py                        # Menu interattivo
    python menu.py .env.cliente           # Profilo specifico

Tutte le funzioni di business logic sono nei moduli commands/.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime as _datetime
from typing import Dict

from app.config import config, HOME_CONFIG_FILE
from app.database import configure_database
from app.engine import FEScraperEngine
from app.log_config import (
    get_logger,
    set_active_log_file,
    get_log_file_path,
)
# Le utility condivise (ragione sociale, rinomina cartelle) sono in commands.__init__
from commands import (  # noqa: F401 — importate per disponibilità nei moduli figli
    _estrai_e_salva_ragione_sociale,
    _rinomina_cartelle,
)

_log = get_logger("menu")


def main():
    env_file = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else None

    # Config
    config.load(env_file)
    _default_year = str(_datetime.now().year)
    cfg = {
        "CF": config.get("CF"),
        "PIN": config.get("PIN"),
        "PASSWORD": config.get("PASSWORD"),
        "DATA_DAL": config.get("DATA_DAL", f"01/01/{_default_year}"),
        "DATA_AL": config.get("DATA_AL", f"31/12/{_default_year}"),
        "DB": config.get("DB", "0"),
        "WRITE": config.get("WRITE", "1"),
        "CORRISPETTIVI": config.get("CORRISPETTIVI", "0"),
        "TRANSFRONTALIERE": config.get("TRANSFRONTALIERE", "0"),
    }

    if not all([cfg["CF"], cfg["PIN"], cfg["PASSWORD"]]):
        print(f"ERRORE: CF, PIN, PASSWORD obbligatori. Verifica {HOME_CONFIG_FILE} o variabili FEC_*.")
        sys.exit(1)

    if config.get("DAILY") == "1":
        from datetime import timedelta
        today = _datetime.now().strftime("%d/%m/%Y")
        yesterday = (_datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        cfg["DATA_DAL"] = yesterday
        cfg["DATA_AL"] = today

    # Logging
    log_path = get_log_file_path(env_file or ".env")
    set_active_log_file(log_path)
    configure_database(env_file)

    # Login
    _log.info("Login per %s...", cfg["CF"])
    engine = FEScraperEngine(_log.info)
    engine.login(cfg["CF"], cfg["PIN"], cfg["PASSWORD"])

    # Verifica deleghe all'avvio
    try:
        from app.deleghe_reader import check_delegations_validity
        esito = check_delegations_validity(engine._request_with_x_appl, _log.info)
        if esito.get("status") == "cambiato":
            print(f"\n⚠️  ATTENZIONE: {esito['messaggio']}")
            print("   Usa l'opzione 'Deleghe > Estrai' per aggiornare l'elenco.\n")
        elif esito.get("status") == "ok":
            _log.info("Verifica deleghe: %s", esito.get("messaggio"))
    except Exception as exc:
        _log.info("Verifica deleghe all'avvio non disponibile: %s", exc)

    # ── Menu loop ──────────────────────────────────────────────────────────
    while True:
        try:
            print("\n" + "=" * 60)
            print("  FEC_ADE — Menu Principale")
            print("=" * 60)
            print("  1. Download Fatture")
            print("  2. F24 Bolli (imposta di bollo)")
            print("  3. Cassetto Fiscale (F24 generici, CU, dichiarazioni)")
            print("  4. Deleghe (estrai, report scadenze)")
            print("  q. Esci")
            print("=" * 60)
            scelta = input("\nScelta: ").strip().lower()

            if scelta == "q":
                print("Uscita.")
                sys.exit(0)

            elif scelta == "1":
                from commands.fatture import run_interactive
                run_interactive(cfg, engine)

            elif scelta == "2":
                from commands.f24 import run_bolli_interactive
                run_bolli_interactive(engine, cfg)

            elif scelta == "3":
                _menu_cassetto(engine, cfg)

            elif scelta == "4":
                _menu_deleghe(engine, cfg)

            else:
                print("Scelta non valida.")

        except KeyboardInterrupt:
            print("\n\nInterrotto dall'utente.")
            sys.exit(0)
        except Exception as e:
            _log.error("ERRORE CRITICO: %s", e)
            print(f"\nErrore: {e}")


def _menu_cassetto(engine: FEScraperEngine, cfg: Dict[str, str]):
    """Sottomenu Cassetto Fiscale."""
    while True:
        print("\n--- CASSETTO FISCALE ---")
        print("  1. F24 Generici (tasse/contributi)")
        print("  2. Certificazioni Uniche (CU)")
        print("  3. Dichiarazioni dei Redditi (RED, 730, 770, IVA, ...)")
        print("  0. TORNA AL MENU PRINCIPALE")
        sub = input("\nScelta: ").strip()

        if sub == "0":
            return
        elif sub == "1":
            from commands.cassetto import run_f24_generici_interactive
            run_f24_generici_interactive(engine, cfg)
        elif sub == "2":
            from commands.cassetto import run_cu_interactive
            run_cu_interactive(engine, cfg)
        elif sub == "3":
            from commands.cassetto import run_dichiarazioni_interactive
            run_dichiarazioni_interactive(engine, cfg)
        else:
            print("Scelta non valida.")


def _menu_deleghe(engine: FEScraperEngine, cfg: Dict[str, str]):
    """Sottomenu Deleghe."""
    while True:
        print("\n--- DELEGHE ---")
        print("  1. Estrai deleghe in JSON")
        print("  2. Report scadenze deleghe")
        print("  0. TORNA AL MENU PRINCIPALE")
        sub = input("\nScelta: ").strip()

        if sub == "0":
            return
        elif sub == "1":
            from commands.deleghe import run_estrai_interactive
            run_estrai_interactive(engine, cfg)
        elif sub == "2":
            from commands.deleghe import run_report_interactive
            run_report_interactive(engine, cfg)
        else:
            print("Scelta non valida.")


if __name__ == "__main__":
    main()
