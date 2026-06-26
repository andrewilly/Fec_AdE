"""
Runner — Pipeline di download condivisa tra main.py (CLI) e menu.py (interattivo).

Estrae la logica comune di:
  - Login e ottenimento token B2B
  - Download fatture (RICEVUTE, EMESSE, TRANSFRONTALIERE)
  - Download corrispettivi
  - Meccanismo retry con persistenza su file JSON
  - Report finale e cleanup

Utilizzo (da main.py o menu.py):
    from app.runner import run_pipeline, PipelineConfig

    cfg = PipelineConfig.from_dict({...})
    success = run_pipeline(cfg, logger_func)
"""

import json
import os
import shutil
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple
from app.engine import FEScraperEngine, unix_ms
from app.output_manager import OutputManager
from app import corrispettivi_engine
from app import transfrontaliere_engine

# ─── Costanti ───────────────────────────────────────────────────────────────────
CATEGORIES = ("RICEVUTE", "EMESSE", "RICEVUTE_TRANSFRONTALIERE", "EMESSE_TRANSFRONTALIERE")
JSON_DIR = os.path.join("output", "JSON_extr")


# ═══════════════════════════════════════════════════════════════════════════════
# Configurazione pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class PipelineConfig:
    """Configurazione per una singola esecuzione della pipeline di download."""

    def __init__(
        self,
        cf: str,
        pin: str,
        password: str,
        piva: str,
        data_dal: str,
        data_al: str,
        motore: str = "INTERMEDIARIO",
        tipo: str = "FOL",
        daily: bool = False,
        db_enabled: bool = False,
        write: bool = True,
        corrispettivi: bool = False,
        transfrontaliere: bool = False,
        profile_name: str = "default",
    ):
        self.cf = cf
        self.pin = pin
        self.password = password
        self.piva = piva
        self.data_dal = data_dal
        self.data_al = data_al
        self.motore = motore
        self.tipo = tipo
        self.daily = daily
        self.db_enabled = db_enabled
        self.write = write
        self.corrispettivi = corrispettivi
        self.transfrontaliere = transfrontaliere
        self.profile_name = profile_name

    @property
    def anno(self) -> int:
        """Anno di competenza estratto da data_dal (formato DD/MM/YYYY)."""
        try:
            return int(self.data_dal.split("/")[2])
        except (ValueError, IndexError):
            return datetime.now().year

    @classmethod
    def from_dict(cls, cfg: Dict[str, str], **overrides) -> "PipelineConfig":
        """Crea una PipelineConfig da un dizionario (es. da parse_inputs)."""
        # Prende il valore da overrides se presente, altrimenti da cfg, altrimenti default
        def _val(key: str, default: str = "") -> str:
            if key in overrides:
                return overrides[key] or default
            return cfg.get(key.upper(), default)

        return cls(
            cf=_val("cf"),
            pin=_val("pin"),
            password=_val("password"),
            piva=_val("piva"),
            data_dal=_val("data_dal"),
            data_al=_val("data_al"),
            motore=_val("motore", "INTERMEDIARIO"),
            tipo=_val("tipo", "FOL"),
            daily=cfg.get("DAILY", "0") == "1",
            db_enabled=cfg.get("DB", "0") == "1",
            write=cfg.get("WRITE", "1") != "0",
            corrispettivi=cfg.get("CORRISPETTIVI", "0") == "1",
            transfrontaliere=cfg.get("TRANSFRONTALIERE", "0") == "1",
            profile_name=overrides.get("profile_name") or cfg.get("PROFILE_NAME", "default"),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Failure store (persistenza download falliti per retry)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_failure_store_path(piva: str) -> str:
    return os.path.join(JSON_DIR, f"download_failures_{piva}.json")


def _empty_failure_store(piva: str) -> Dict[str, object]:
    return {
        "piva": piva,
        "updated_at": "",
        "categories": {category: [] for category in CATEGORIES},
    }


def _failure_key(item: Dict[str, object]) -> tuple:
    return (
        str(item.get("category", "")),
        str(item.get("idFattura", "")),
        str(item.get("tipoInvio", "")),
        str(item.get("tipoFile", "FILE_FATTURA")),
    )


def _load_failure_store(path: str, piva: str) -> Dict[str, object]:
    store = _empty_failure_store(piva)
    if not os.path.exists(path):
        return store

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return store

    if "categories" in data and isinstance(data.get("categories"), dict):
        categories = data["categories"]
        store["updated_at"] = str(data.get("updated_at", ""))
        store["piva"] = str(data.get("piva", piva))
        for category in CATEGORIES:
            items = categories.get(category, [])
            if isinstance(items, list):
                normalized = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    n = dict(item)
                    n["category"] = category
                    n["tipoFile"] = str(n.get("tipoFile", "FILE_FATTURA"))
                    normalized.append(n)
                store["categories"][category] = normalized
        return store

    category = data.get("category")
    failed_struct = data.get("failed_struct", [])
    if category in CATEGORIES and isinstance(failed_struct, list):
        normalized = []
        for item in failed_struct:
            if not isinstance(item, dict):
                continue
            n = dict(item)
            n["category"] = category
            n["tipoFile"] = str(n.get("tipoFile", "FILE_FATTURA"))
            normalized.append(n)
        store["categories"][category] = normalized
    return store


def _save_failure_store(path: str, store: Dict[str, object]) -> None:
    categories = store.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}

    for category in CATEGORIES:
        items = categories.get(category, [])
        if isinstance(items, list):
            categories[category] = sorted(
                [item for item in items if isinstance(item, dict)],
                key=lambda item: (
                    str(item.get("idFattura", "")),
                    str(item.get("tipoInvio", "")),
                    str(item.get("tipoFile", "")),
                ),
            )
        else:
            categories[category] = []

    store["categories"] = categories
    store["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _collect_failed_entries(stats: Dict[str, object], category: str, run_ts: str) -> List[Dict[str, object]]:
    failed_struct = stats.get("failed_struct", [])
    if not isinstance(failed_struct, list):
        return []
    entries: List[Dict[str, object]] = []
    for item in failed_struct:
        if not isinstance(item, dict):
            continue
        n = dict(item)
        n["category"] = category
        n["tipoFile"] = str(n.get("tipoFile", "FILE_FATTURA"))
        n["first_seen"] = run_ts
        n["last_seen"] = run_ts
        n["attempts"] = 1
        entries.append(n)
    return entries


def _merge_failure_entries(
    existing: List[Dict[str, object]],
    new_entries: List[Dict[str, object]],
    run_ts: str,
) -> List[Dict[str, object]]:
    merged = {_failure_key(item): dict(item) for item in existing if isinstance(item, dict)}
    for item in new_entries:
        key = _failure_key(item)
        if key in merged:
            current = merged[key]
            current["status"] = item.get("status", current.get("status"))
            current["url"] = item.get("url", current.get("url"))
            current["last_seen"] = run_ts
            current["attempts"] = int(current.get("attempts", 1)) + 1
        else:
            merged[key] = dict(item)
    return list(merged.values())


def _build_retry_data(entries: List[Dict[str, object]]) -> Dict[str, object]:
    fatture = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("idFattura", "")), str(item.get("tipoInvio", "")))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        fatture.append({"idFattura": key[0], "tipoInvio": key[1]})
    return {"totaleFatture": len(fatture), "fatture": fatture}


def _apply_retry_results(
    pending_entries: List[Dict[str, object]],
    retry_stats: Dict[str, object],
    run_ts: str,
) -> Tuple[List[Dict[str, object]], set]:
    failed_struct = retry_stats.get("failed_struct", [])
    failed_map = {}
    if isinstance(failed_struct, list):
        failed_map = {_failure_key(item): item for item in failed_struct if isinstance(item, dict)}

    remaining = []
    recovered = set()
    for item in pending_entries:
        key = _failure_key(item)
        if key not in failed_map:
            recovered.add(key)
            continue
        updated = dict(item)
        latest = failed_map[key]
        updated["status"] = latest.get("status", updated.get("status"))
        updated["url"] = latest.get("url", updated.get("url"))
        updated["last_seen"] = run_ts
        updated["attempts"] = int(updated.get("attempts", 1)) + 1
        remaining.append(updated)

    return remaining, recovered


def _has_pending_failures(store: Dict[str, object]) -> bool:
    categories = store.get("categories", {})
    if not isinstance(categories, dict):
        return False
    return any(
        isinstance(categories.get(cat), list) and categories[cat]
        for cat in CATEGORIES
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline principale
# ═══════════════════════════════════════════════════════════════════════════════


def run_pipeline(
    cfg: PipelineConfig,
    logger_func: Callable[..., None],
    engine: Optional[FEScraperEngine] = None,
) -> bool:
    """
    Esegue la pipeline completa di download fatture per un profilo.

    Args:
        cfg: configurazione della pipeline
        logger_func: funzione per il logging
        engine: (opzionale) engine già autenticato; se None, ne crea uno nuovo

    Returns:
        True se il profilo è completato (anche con errori parziali),
        False solo in caso di errore critico.
    """
    profile_name = cfg.profile_name
    logger_func(f"Avvio pipeline per profilo: {profile_name}")
    logger_func(
        f"  PIVA: {cfg.piva or '(dal wizard)'} | Motore: {cfg.motore} "
        f"| Periodo: {cfg.data_dal} → {cfg.data_al}"
    )
    logger_func(
        f"  Corrispettivi: {cfg.corrispettivi} "
        f"| Transfrontaliere: {cfg.transfrontaliere} | DB: {cfg.db_enabled}"
    )

    if cfg.daily:
        from datetime import timedelta
        today = datetime.now().strftime("%d/%m/%Y")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        cfg.data_dal = yesterday
        cfg.data_al = today
        logger_func(f"Modalità DAILY: {yesterday} → {today}")

    # ── Setup ──────────────────────────────────────────────────────────────
    os.makedirs(JSON_DIR, exist_ok=True)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    own_engine = False
    if engine is None:
        engine = FEScraperEngine(logger_func)
        own_engine = True

    try:
        # ── Login e selezione motore ───────────────────────────────────────
        if own_engine:
            p_auth = engine.login(cfg.cf, cfg.pin, cfg.password)
            cfg.piva = engine.select_engine(cfg.motore, p_auth, cfg.piva, cfg.tipo)
        else:
            # Engine già autenticato (da menu.py): select_engine senza login
            cfg.piva = engine.select_engine(cfg.motore, "", cfg.piva, cfg.tipo)

        logger_func(f"PIVA operativa: {cfg.piva}")

        # ── Failure store ──────────────────────────────────────────────────
        failure_store_path = _get_failure_store_path(cfg.piva)
        failure_store = _load_failure_store(failure_store_path, cfg.piva)
        pending_before_run = {
            category: [dict(item) for item in failure_store["categories"].get(category, [])]
            for category in CATEGORIES
        }

        # ── Output manager ────────────────────────────────────────────────
        output = OutputManager(cfg.piva, logger_func, anno=cfg.anno, db_enabled=cfg.db_enabled)

        # ── Token B2B ──────────────────────────────────────────────────────
        engine.get_b2b_tokens()

        # ── Corrispettivi ──────────────────────────────────────────────────
        if cfg.corrispettivi:
            corrispettivi_engine.run(
                session=engine.session,
                headers_cons=engine.headers_cons,
                dal=cfg.data_dal,
                al=cfg.data_al,
                piva=cfg.piva,
                anno=cfg.anno,
                cfg={"WRITE": "1" if cfg.write else "0", "DB": "1" if cfg.db_enabled else "0"},
                unix_ms_func=unix_ms,
                logger=logger_func,
                output_root="output",
                run_ts=run_ts,
                cf=cfg.cf,
                pin=cfg.pin,
                password=cfg.password,
            )

        # ── Download fatture RICEVUTE ──────────────────────────────────────
        stats_ric = _download_category(
            engine, output, cfg.data_dal, cfg.data_al, "RICEVUTE", run_ts, logger_func, JSON_DIR,
        )

        # ── Download fatture EMESSE ─────────────────────────────────────────
        stats_eme = _download_category(
            engine, output, cfg.data_dal, cfg.data_al, "EMESSE", run_ts, logger_func, JSON_DIR,
        )

        # ── Transfrontaliere ───────────────────────────────────────────────
        stats_ric_tf: Dict[str, object] = {"found": 0, "downloaded": 0}
        stats_eme_tf: Dict[str, object] = {"found": 0, "downloaded": 0}
        data_ric_tf: Dict[str, object] = {"totaleFatture": 0, "fatture": []}
        data_eme_tf: Dict[str, object] = {"totaleFatture": 0, "fatture": []}

        if cfg.transfrontaliere:
            stats_eme_tf, data_eme_tf = _download_tf_category(
                engine, output, cfg.data_dal, cfg.data_al, "EMESSE", cfg.piva,
                run_ts, logger_func, JSON_DIR,
            )
            stats_ric_tf, data_ric_tf = _download_tf_category(
                engine, output, cfg.data_dal, cfg.data_al, "RICEVUTE", cfg.piva,
                run_ts, logger_func, JSON_DIR,
            )

        # ── Raccolta statistiche ───────────────────────────────────────────
        stats_by_category = {
            "RICEVUTE": stats_ric,
            "EMESSE": stats_eme,
            "RICEVUTE_TRANSFRONTALIERE": stats_ric_tf,
            "EMESSE_TRANSFRONTALIERE": stats_eme_tf,
        }
        data_by_category = {
            "RICEVUTE": {"totaleFatture": stats_ric.get("found", 0), "fatture": []},
            "EMESSE": {"totaleFatture": stats_eme.get("found", 0), "fatture": []},
            "RICEVUTE_TRANSFRONTALIERE": data_ric_tf,
            "EMESSE_TRANSFRONTALIERE": data_eme_tf,
        }

        # ── Retry download falliti da run precedenti ───────────────────────
        recovered_keys_by_category = {category: set() for category in CATEGORIES}

        for category in CATEGORIES:
            pending_items = pending_before_run.get(category, [])
            if not pending_items:
                continue

            logger_func(
                f"Retry {category}: tentativo recupero {len(pending_items)} fatture pendenti "
                f"da {os.path.basename(failure_store_path)}"
            )
            retry_data = _build_retry_data(pending_items)
            retry_stats = output.download_invoices_set(
                engine.session, retry_data, category, engine.headers_token, unix_ms,
            )
            remaining, recovered = _apply_retry_results(pending_items, retry_stats, run_ts)
            logger_func(f"Retry {category}: recuperate {len(recovered)}, ancora pendenti {len(remaining)}")
            failure_store["categories"][category] = remaining
            recovered_keys_by_category[category] = recovered

        # ── Aggiornamento failure store con nuovi fallimenti ───────────────
        for category in CATEGORIES:
            new_failed = [
                item
                for item in _collect_failed_entries(stats_by_category[category], category, run_ts)
                if _failure_key(item) not in recovered_keys_by_category[category]
            ]
            failure_store["categories"][category] = _merge_failure_entries(
                failure_store["categories"].get(category, []),
                new_failed,
                run_ts,
            )

        # ── Salva/rimuovi failure store ────────────────────────────────────
        if _has_pending_failures(failure_store):
            _save_failure_store(failure_store_path, failure_store)
            counts = ", ".join(
                f"{cat}={len(failure_store['categories'][cat])}"
                for cat in CATEGORIES
            )
            logger_func(f"File pendenti aggiornato: {os.path.basename(failure_store_path)} ({counts})")
        elif os.path.exists(failure_store_path):
            os.remove(failure_store_path)
            logger_func(f"Nessun download pendente: rimosso {os.path.basename(failure_store_path)}")

        # ── Report finale ──────────────────────────────────────────────────
        logger_func("\n" + "=" * 50)
        logger_func("RIEPILOGO PROCESSO")
        logger_func("=" * 50)
        for category in CATEGORIES:
            if data_by_category[category]["totaleFatture"] > 0:
                output.final_check(category, stats_by_category[category])  # type: ignore[arg-type]

        # ── Cleanup ────────────────────────────────────────────────────────
        if not cfg.write:
            if os.path.isdir(output.root_path):
                shutil.rmtree(output.root_path)
                logger_func(f"WRITE=0: rimossa cartella output {output.root_path}")

        logger_func(f"\nProfilo completato: {profile_name}")
        return True

    except Exception as e:
        logger_func(f"ERRORE CRITICO [{profile_name}]: {e}")
        return False


def _download_category(
    engine: FEScraperEngine,
    output: OutputManager,
    dal: str,
    al: str,
    category: str,
    run_ts: str,
    logger_func: Callable[..., None],
    json_dir: str,
) -> Dict[str, object]:
    """Scarica una categoria di fatture (RICEVUTE/EMESSE)."""
    data = engine.fetch_invoices(dal, al, category)
    out_path = os.path.join(json_dir, f"fatture_{category.lower()}_{run_ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    stats: Dict[str, object] = {"found": 0, "downloaded": 0}
    if data["totaleFatture"] > 0:
        stats = output.download_invoices_set(
            engine.session, data, category, engine.headers_token, unix_ms,
        )
    else:
        logger_func(f"Nessuna fattura {category} trovata.")
    return stats


def _download_tf_category(
    engine: FEScraperEngine,
    output: OutputManager,
    dal: str,
    al: str,
    category: str,
    piva: str,
    run_ts: str,
    logger_func: Callable[..., None],
    json_dir: str,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Scarica una categoria di fatture transfrontaliere."""
    data = transfrontaliere_engine.fetch(
        engine.session, engine.headers_cons, dal, al, category,
        piva=piva, logger=logger_func,
    )
    out_path = os.path.join(json_dir, f"fatture_{category.lower()}_transfrontaliere_{run_ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    stats: Dict[str, object] = {"found": 0, "downloaded": 0}
    if data["totaleFatture"] > 0:
        stats = output.download_invoices_set(
            engine.session, data, f"{category}_TRANSFRONTALIERE",
            engine.headers_token, unix_ms,
        )
    else:
        logger_func(f"Nessuna fattura {category} TRANSFRONTALIERE trovata.")
    return stats, data


__all__ = ["run_pipeline", "PipelineConfig", "CATEGORIES"]
