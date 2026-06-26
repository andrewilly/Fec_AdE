"""
ConfigManager — Gestione centralizzata della configurazione di Fec_AdE.

Cerca le credenziali e le impostazioni in quest'ordine:
  1. Variabili d'ambiente (prefisso FEC_)
  2. File ~/.fec_ade/config.env
  3. File ./.env nella directory del progetto (SOLO sviluppo)

Utilizzo:
    from app.config import config

    cf = config.get("CF")
    pin = config.get("PIN")
    pwd = config.get("PASSWORD")
"""

import os
from typing import Dict, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

# ─── Percorsi sicuri ───────────────────────────────────────────────────────────
HOME_CONFIG_DIR = os.path.expanduser("~/.fec_ade")
HOME_CONFIG_FILE = os.path.join(HOME_CONFIG_DIR, "config.env")
LOCAL_CONFIG_FILE = ".env"  # Solo sviluppo, sarà bloccato da .gitignore


# ─── Mappa variabili d'ambiente → chiavi config ────────────────────────────────
ENV_PREFIX = "FEC_"
ENV_MAP: Dict[str, str] = {
    "FEC_CF": "CF",
    "FEC_PIN": "PIN",
    "FEC_PASSWORD": "PASSWORD",
    "FEC_PIVA": "PIVA",
    "FEC_DATA_DAL": "DATA_DAL",
    "FEC_DATA_AL": "DATA_AL",
    "FEC_UTENZA": "UTENZA",
    "FEC_TIPO": "TIPO",
    "FEC_DAILY": "DAILY",
    "FEC_CORRISPETTIVI": "CORRISPETTIVI",
    "FEC_TRANSFRONTALIERE": "TRANSFRONTALIERE",
    "FEC_DB": "DB",
    "FEC_WRITE": "WRITE",
    "FEC_DB_TYPE": "DB_TYPE",
    "FEC_DB_SQLITE_PATH": "DB_SQLITE_PATH",
    "FEC_DB_HOST": "DB_HOST",
    "FEC_DB_PORT": "DB_PORT",
    "FEC_DB_NAME": "DB_NAME",
    "FEC_DB_USER": "DB_USER",
    "FEC_DB_PASS": "DB_PASS",
    "FEC_DELEGHE_FILE": "DELEGHE_FILE",
}


def _load_dotenv_file(path: str) -> Dict[str, str]:
    """Carica un file .env e restituisce un dict."""
    out: Dict[str, str] = {}
    if not os.path.isfile(path):
        return out

    if load_dotenv is not None:
        load_dotenv(path, override=False)
        # Dopo load_dotenv le variabili sono in os.environ
        # ma noi leggiamo il file manualmente per avere il dict

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class ConfigManager:
    """Gestore configurazione thread-safe (read-only dopo init)."""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}
        self._loaded = False
        self._sources: list[str] = []

    def load(self, env_override: Optional[str] = None) -> "ConfigManager":
        """
        Carica la configurazione dalle sorgenti con priorità crescente:
        ultima sorgente caricata ha priorità massima.

        Priorità (dal basso all'alto):
          1. ~/.fec_ade/config.env    (base sicura)
          2. ./.env locale            (solo sviluppo, bloccato da .gitignore)
          3. env_override             (profilo specifico per multi-azienda)
          4. Variabili d'ambiente     (massima priorità: FEC_CF, FEC_PIN, ...)
        """
        self._data = {}
        self._sources = []

        # 1) File ~/.fec_ade/config.env (base — sempre caricato se esiste)
        if os.path.isfile(HOME_CONFIG_FILE):
            file_cfg = _load_dotenv_file(HOME_CONFIG_FILE)
            self._data.update(file_cfg)
            self._sources.append(f"file:{HOME_CONFIG_FILE}")

        # 2) File ./.env locale (SOLO se non c'è override esplicito)
        if not env_override and os.path.isfile(LOCAL_CONFIG_FILE):
            file_cfg = _load_dotenv_file(LOCAL_CONFIG_FILE)
            self._data.update(file_cfg)
            self._sources.append(f"file:{LOCAL_CONFIG_FILE}")
            import warnings
            warnings.warn(
                f"Caricato file {LOCAL_CONFIG_FILE} locale. "
                "In produzione usa ~/.fec_ade/config.env o variabili d'ambiente.",
                UserWarning,
                stacklevel=2,
            )

        # 3) File esplicito (override per profilo multi-azienda)
        if env_override and os.path.isfile(env_override):
            file_cfg = _load_dotenv_file(env_override)
            self._data.update(file_cfg)
            self._sources.append(f"file:{env_override}")
            # Carica anche in environ per compatibilità (senza sovrascrivere)
            for k, v in file_cfg.items():
                if k not in os.environ:
                    os.environ[k] = v

        # 4) Variabili d'ambiente (massima priorità — sovrascrive tutto)
        env_found = 0
        for env_key, cfg_key in ENV_MAP.items():
            val = os.environ.get(env_key)
            if val is not None:
                self._data[cfg_key] = val
                env_found += 1
        if env_found:
            self._sources.append(f"env:{env_found} variabili FEC_*")

        self._loaded = True
        return self

    # ─── Accesso lettura ────────────────────────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self._data.get(key, str(default))
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self._data.get(key, "1" if default else "0")
        return raw.strip() in ("1", "true", "True", "yes", "Yes")

    def get_list(self, key: str, separator: str = ",", default: Optional[list[str]] = None) -> list[str]:
        if default is None:
            default = []
        raw = self._data.get(key)
        if not raw:
            return default
        return [item.strip() for item in raw.split(separator) if item.strip()]

    def to_dict(self) -> Dict[str, str]:
        return dict(self._data)

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    # ─── Validazione ────────────────────────────────────────────────────────

    def validate(self, required: list[str], profile_name: str = "default") -> None:
        """Solleva ValueError se mancano campi obbligatori."""
        missing = [k for k in required if not self.get(k)]
        if missing:
            raise ValueError(
                f"Parametri mancanti nel profilo '{profile_name}': "
                f"{', '.join(missing)}. "
                f"Verifica ~/.fec_ade/config.env, variabili FEC_*, o il file di profilo."
            )

    def get_required(self, key: str, profile_name: str = "default") -> str:
        val = self.get(key)
        if not val:
            raise ValueError(
                f"Parametro '{key}' obbligatorio mancante nel profilo '{profile_name}'."
            )
        return val

    def __repr__(self) -> str:
        src = ", ".join(self._sources) if self._sources else "nessuna"
        return f"<ConfigManager loaded={self._loaded} sources=[{src}] keys={len(self._data)}>"


# ─── Singleton di modulo ───────────────────────────────────────────────────────
config = ConfigManager()

__all__ = ["config", "ConfigManager", "HOME_CONFIG_DIR", "HOME_CONFIG_FILE"]
