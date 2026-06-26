"""
Fec_AdE — Comandi CLI condivisi.

Fornisce le funzioni di utilità comune per tutti i comandi:
  - login engine
  - selezione clienti
  - utility output/anni
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.config import config
from app.engine import FEScraperEngine
from app.log_config import get_logger
from app.database import configure_database
from app.deleghe_reader import (
    RAGIONI_SOCIALI_FILE,
    TIPO_DELEGA_INCARICATO,
    TIPO_DELEGA_DIRETTA,
)

_log = get_logger("commands")


# ─── Login ─────────────────────────────────────────────────────────────────────


def login_engine(
    cf: str,
    pin: str,
    password: str,
    logger_func: Optional[Callable[..., None]] = None,
) -> FEScraperEngine:
    """Crea un engine e fa login. Restituisce l'engine autenticato."""
    log = logger_func or _log.info
    engine = FEScraperEngine(log)
    engine.login(cf, pin, password)
    return engine


# ─── Clienti ───────────────────────────────────────────────────────────────────


def load_clienti_incaricato(engine: FEScraperEngine) -> List[Dict[str, Any]]:
    """Carica clienti dal wizard Incaricato."""
    from app.deleghe_reader import fetch_clients_from_wizard
    return fetch_clients_from_wizard(
        engine._request_with_x_appl,
        _log.info,
    )


def load_clienti_delega_diretta(
    cfs: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Carica clienti da CF manuali o da CSV."""
    if cfs:
        mappa_rs = _load_ragioni_sociali()
        return [{
            "cf": cf, "tipo": "FOL",
            "motore": "DELEGA_DIRETTA",
            "ragione_sociale": mappa_rs.get(cf, ""),
        } for cf in cfs]
    # Carica da CSV
    from app.deleghe_reader import load_deleghe_from_csv
    deleghe = load_deleghe_from_csv()
    if not deleghe:
        print("Nessuna delega trovata in ~/.fec_ade/deleghe.csv")
        return []
    return [{
        "cf": d["cf"], "tipo": "FOL",
        "motore": "DELEGA_DIRETTA",
        "ragione_sociale": d.get("ragione_sociale", ""),
    } for d in deleghe]


def load_clienti_completi(engine: FEScraperEngine) -> List[Dict[str, Any]]:
    """Carica TUTTI i clienti (incaricato + delega diretta)."""
    from app.deleghe_reader import fetch_all_deleghe_enhanced
    clienti, _ = fetch_all_deleghe_enhanced(
        engine=engine,
        logger_func=_log.info,
    )
    return clienti


def _load_ragioni_sociali() -> Dict[str, str]:
    if os.path.exists(RAGIONI_SOCIALI_FILE):
        with open(RAGIONI_SOCIALI_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def scegli_cliente_interattivo(
    clienti: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Mostra lista clienti e lascia selezionare. Restituisce lista (1+ clienti)."""
    if not clienti:
        print("Nessun cliente disponibile.")
        return None

    print("\n" + "=" * 70)
    print("  CLIENTI DISPONIBILI")
    print("=" * 70)
    for i, cli in enumerate(clienti, 1):
        rs = cli.get("ragione_sociale", "") or ""
        tipo_d = cli.get("tipo_delega", cli.get("motore", "?"))
        if rs:
            print(f"  {i:3d}. {cli['cf']:18s}  {rs:30s}  ({tipo_d[:4]})")
        else:
            print(f"  {i:3d}. {cli['cf']:18s}  {'(nome sconosciuto)':30s}  ({tipo_d[:4]})")
    print("  a. TUTTI")
    print("  n. Numeri (es. 1,3,5-10)")
    print("  0. TORNA INDIETRO")
    print("=" * 70)

    scelta = input(f"\nSeleziona (1-{len(clienti)} / a / n / 0): ").strip().lower()

    if scelta == "0":
        return None
    elif scelta == "a":
        return clienti
    elif scelta == "n":
        raw = input("Numeri (es. 1,3,5-10): ").strip()
        if not raw:
            return None
        indici = set()
        for parte in raw.replace(";", ",").split(","):
            parte = parte.strip()
            if "-" in parte:
                try:
                    a, b = parte.split("-", 1)
                    for idx in range(int(a.strip()), int(b.strip()) + 1):
                        if 1 <= idx <= len(clienti):
                            indici.add(idx - 1)
                except ValueError:
                    pass
            else:
                try:
                    idx = int(parte)
                    if 1 <= idx <= len(clienti):
                        indici.add(idx - 1)
                except ValueError:
                    pass
        if not indici:
            print("Nessun indice valido.")
            return None
        return [clienti[idx] for idx in sorted(indici)]
    else:
        try:
            idx = int(scelta) - 1
            if 0 <= idx < len(clienti):
                return [clienti[idx]]
            print("Scelta non valida.")
            return None
        except ValueError:
            print("Scelta non valida.")
            return None


# ─── Utility ────────────────────────────────────────────────────────────────────


def parse_anno(raw: str) -> Optional[int]:
    raw = raw.strip()
    if not raw:
        return datetime.now().year
    if raw.isdigit() and len(raw) == 4:
        return int(raw)
    print("Anno non valido. Usa formato YYYY (es. 2025).")
    return None


def input_anno(prompt: str = "Anno (INVIO = anno corrente): ") -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return datetime.now().year
        if raw.isdigit() and len(raw) == 4:
            return int(raw)
        print("Anno non valido. Usa formato YYYY (es. 2025).")


def input_piva(prompt: str = "Partita IVA cliente: ") -> Optional[str]:
    piva = input(prompt).strip()
    if not piva:
        print("P.IVA non valida.")
        return None
    return piva


# ─── Esecuzione deleghe ────────────────────────────────────────────────────────


def esegui_deleghe(
    clienti: List[Dict[str, Any]],
    cfg: Dict[str, str],
    engine: FEScraperEngine,
):
    """Esegue il download per una lista di clienti con engine già autenticato."""
    from app.runner import run_pipeline, PipelineConfig

    for idx, cli in enumerate(clienti, 1):
        rs = cli.get("ragione_sociale", "") or ""
        label = f"{cli['cf']} ({rs})" if rs else cli['cf']
        print(f"\n--- Delega {idx}/{len(clienti)}: {label} ---")

        motore = cli.get("motore", "INTERMEDIARIO")
        pipe_cfg = PipelineConfig.from_dict(
            cfg,
            piva=cli["cf"],
            motore=motore,
            tipo=cli.get("tipo", "FOL"),
        )
        try:
            success = run_pipeline(pipe_cfg, _log.info, engine=engine)
            if success:
                _estrai_e_salva_ragione_sociale(pipe_cfg.piva, cli["cf"])
                _rinomina_cartelle(pipe_cfg.piva)
        except Exception as e:
            _log.error("ERRORE per %s: %s", cli['cf'], e)


# ─── Utility ragione sociale (condivise con menu.py) ──────────────────────────


def _get_denominazione_da_xml(xml_path: str) -> str:
    """Estrae la denominazione (ragione sociale) da un file XML fattura."""
    try:
        from lxml import etree
        tree = etree.parse(xml_path)
        root = tree.getroot()
        path_lower = xml_path.replace("\\", "/").lower()
        is_emesse = "/fattureemesse/" in path_lower or "/emesse/" in path_lower
        is_ricevute = "/fatturericevute/" in path_lower or "/ricevute/" in path_lower
        if is_emesse:
            tag = "CedentePrestatore"
        else:
            tag = "CessionarioCommittente"
        el = root.xpath(f".//*[local-name()='{tag}']")
        if not el:
            return ""
        el = el[0]
        denom = el.xpath(".//*[local-name()='Denominazione']")
        if denom and denom[0].text:
            return denom[0].text.strip()
        nome = el.xpath(".//*[local-name()='Nome']")
        cognome = el.xpath(".//*[local-name()='Cognome']")
        if nome or cognome:
            n = (nome[0].text or "").strip() if nome else ""
            c = (cognome[0].text or "").strip() if cognome else ""
            return f"{n} {c}".strip()
    except Exception:
        pass
    return ""


def _estrai_e_salva_ragione_sociale(piva: str, cf: str):
    """Cerca la ragione sociale dagli XML scaricati e la salva."""
    from app.deleghe_reader import salva_ragione_sociale
    root = os.path.join("output", piva)
    if not os.path.isdir(root):
        return
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".xml"):
                rs = _get_denominazione_da_xml(os.path.join(dirpath, f))
                if rs:
                    salva_ragione_sociale(piva, cf, rs)
                    _log.info("Ragione sociale rilevata: %s", rs)
                    return


def _cartella_safe(nome: str) -> str:
    val = nome.strip().upper()
    for c in r'\/:*?"<>|':
        val = val.replace(c, "_")
    val = val.replace(" ", "_")
    return val


def _rinomina_cartelle(piva: str):
    """Rinomina la cartella output/{PIVA} in output/{RAGIONE_SOCIALE}."""
    if not os.path.exists(RAGIONI_SOCIALI_FILE):
        return
    with open(RAGIONI_SOCIALI_FILE, "r", encoding="utf-8") as f:
        rs_map = json.load(f)
    rs = rs_map.get(piva, "")
    if not rs:
        return
    rs_safe = _cartella_safe(rs)

    old = os.path.join("output", piva)
    new = os.path.join("output", rs_safe)
    if old == new or not os.path.isdir(old):
        return

    if os.path.exists(new):
        _log.info("Cartella già esistente: %s — merge da %s", new, old)
        for item in os.listdir(old):
            src = os.path.join(old, item)
            dst = os.path.join(new, item)
            try:
                if os.path.isdir(src):
                    if os.path.exists(dst) and os.path.isdir(dst):
                        for root, _dirs, files in os.walk(src):
                            rel = os.path.relpath(root, src)
                            target_dir = os.path.join(dst, rel) if rel != "." else dst
                            os.makedirs(target_dir, exist_ok=True)
                            for f in files:
                                sf = os.path.join(root, f)
                                df = os.path.join(target_dir, f)
                                if os.path.exists(df):
                                    os.remove(df)
                                shutil.move(sf, df)
                    else:
                        shutil.move(src, dst)
                else:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
            except Exception as e:
                _log.warning("Merge fallito %s → %s: %s", src, dst, e)
        shutil.rmtree(old, ignore_errors=True)
        _log.info("Cartelle unite: %s → %s", old, new)
    else:
        try:
            os.rename(old, new)
            _log.info("Cartella rinominata: %s → %s", old, new)
        except Exception as e:
            _log.warning("Rinomina fallita %s: %s", old, e)


__all__ = [
    "login_engine",
    "load_clienti_incaricato",
    "load_clienti_delega_diretta",
    "load_clienti_completi",
    "scegli_cliente_interattivo",
    "parse_anno",
    "input_anno",
    "input_piva",
    "esegui_deleghe",
]
