#!/usr/bin/env python3
"""
Fec_AdE — CLI per l'accesso ai servizi dell'Agenzia delle Entrate.

Concept and original idea by André Willy Rizzo
Copyright (c) 2026 André Willy Rizzo. All rights reserved.

Utilizzo:
    python cli.py [comando] [sottocomando] [opzioni]
    python cli.py menu                          # Menu interattivo
    python cli.py fatture --incaricato          # Download fatture incaricato
    python cli.py f24 bolli --piva X --anno 2025
    python cli.py cassetto f24-generici --piva X --anno 2025
    python cli.py deleghe estrai
    python cli.py tutto --all --anno 2025

Se nessun comando è specificato, avvia il menu interattivo.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def main():
    parser = argparse.ArgumentParser(
        prog="fec-ade",
        description="Fec_AdE — Accesso CLI ai servizi dell'Agenzia delle Entrate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python cli.py menu                          # Menu interattivo
  python cli.py fatture --incaricato          # Scarica fatture da Incaricato
  python cli.py fatture --delega-diretta CF1 CF2 --anno 2025
  python cli.py f24 bolli --piva 01234567890 --anno 2025 --trimestre 1
  python cli.py cassetto cu --piva CF --anno 2025
  python cli.py cassetto dichiarazioni --piva CF --anno 2025 --tipo RED
  python cli.py deleghe estrai
  python cli.py deleghe report
  python cli.py tutto --cliente CF --anno 2025
        """,
    )

    # Opzioni globali
    parser.add_argument("--env", dest="env_file", default=None,
                        help="Percorso file .env profilo (es. .env.cliente)")
    parser.add_argument("--debug", action="store_true", help="Modalità debug (log DEBUG)")

    # Sottocomandi
    subparsers = parser.add_subparsers(dest="command", help="Comando disponibile")

    # Menu interattivo (default)
    subparsers.add_parser("menu", help="Avvia il menu interattivo")

    # Fatture
    from commands import fatture
    fatture.add_args(subparsers)

    # F24
    from commands import f24
    f24.add_args(subparsers)

    # Cassetto Fiscale
    from commands import cassetto
    cassetto.add_args(subparsers)

    # Deleghe
    from commands import deleghe
    deleghe.add_args(subparsers)

    # Tutto
    from commands import tutto
    tutto.add_args(subparsers)

    args = parser.parse_args()

    # Debug mode
    if args.debug:
        import logging
        logging.getLogger("fec_ade").setLevel(logging.DEBUG)

    # Dispatch
    if args.command is None or args.command == "menu":
        _run_menu(args)
    elif args.command == "fatture":
        from commands.fatture import run
        sys.exit(run(args))
    elif args.command == "f24":
        from commands.f24 import run
        sys.exit(run(args))
    elif args.command == "cassetto":
        from commands.cassetto import run
        sys.exit(run(args))
    elif args.command == "deleghe":
        from commands.deleghe import run
        sys.exit(run(args))
    elif args.command == "tutto":
        from commands.tutto import run
        sys.exit(run(args))
    else:
        parser.print_help()
        sys.exit(1)


def _run_menu(args):
    """Avvia il menu interattivo."""
    from menu import main as menu_main
    menu_main()


if __name__ == "__main__":
    main()
