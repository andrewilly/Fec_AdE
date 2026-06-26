"""
Logging professionale per Fec_AdE.

Fornisce un sistema di logging strutturato con:
  - Livelli: DEBUG, INFO, WARNING, ERROR
  - Rotazione automatica dei file
  - Output su file + stdout
  - Supporto per log separati per profilo (multi-azienda)

Utilizzo:
    from app.log_config import get_logger

    log = get_logger("main")
    log.info("Avvio download...")
    log.error("Errore critico: %s", str(e))
"""

import logging
import os
from logging.handlers import RotatingFileHandler

# ─── Costanti ───────────────────────────────────────────────────────────────────
DEFAULT_LOG_DIR = os.path.join("output", "logs")
DEFAULT_LOG_FILE = "log_esecuzione.txt"
DEFAULT_LOG_LEVEL = logging.INFO
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5

# ─── Logger cache ───────────────────────────────────────────────────────────────
_loggers: dict[str, logging.Logger] = {}
_current_log_path: str = DEFAULT_LOG_FILE


def set_active_log_file(file_path: str) -> str:
    """
    Imposta il file di log attivo (per logging multi-profilo).
    Crea la directory se necessario.
    Restituisce il path canonico.
    """
    global _current_log_path
    _current_log_path = file_path
    log_dir = os.path.dirname(file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    # Ricarica tutti i logger col nuovo path
    for logger in _loggers.values():
        _attach_file_handler(logger)
    return _current_log_path


def get_log_path() -> str:
    return _current_log_path


# ─── Handler personalizzato ─────────────────────────────────────────────────────


class StdoutHandler(logging.Handler):
    """Handler che scrive su stdout con formato semplice."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            print(msg)
        except Exception:
            self.handleError(record)


# ─── Setup interno ──────────────────────────────────────────────────────────────


def _create_formatter(include_logger: bool = False) -> logging.Formatter:
    fmt = "%(asctime)s | %(levelname)s | %(message)s"
    if include_logger:
        fmt = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    return logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")


def _attach_file_handler(logger: logging.Logger) -> None:
    """Rimuove il vecchio file handler e ne aggiunge uno nuovo al path attivo."""
    logger.handlers = [h for h in logger.handlers if not isinstance(h, (RotatingFileHandler, logging.FileHandler))]

    log_dir = os.path.dirname(_current_log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    fh = RotatingFileHandler(
        _current_log_path,
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(_create_formatter(include_logger=True))
    fh.setLevel(DEFAULT_LOG_LEVEL)
    logger.addHandler(fh)


def get_logger(name: str = "app", level: int = DEFAULT_LOG_LEVEL) -> logging.Logger:
    """
    Restituisce (o crea) un logger con `name`.
    Tutti i logger condividono lo stesso file handler attivo e uno stdout handler.
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(f"fec_ade.{name}")
    logger.setLevel(level)
    logger.propagate = False

    # Stdout handler (semplice)
    sh = StdoutHandler()
    sh.setFormatter(_create_formatter(include_logger=False))
    sh.setLevel(level)
    logger.addHandler(sh)

    # File handler (con rotazione)
    _attach_file_handler(logger)

    _loggers[name] = logger
    return logger


def create_profile_logger(profile_name: str, env_file: str) -> logging.Logger:
    """
    Crea un logger dedicato per un profilo multi-azienda.
    Il file di log sarà output/logs/log_{token}.txt.
    """
    token = _sanitize_log_token(profile_name)
    log_dir = os.path.join("output", "logs")
    log_path = os.path.join(log_dir, f"log_{token}.txt")
    set_active_log_file(log_path)
    return get_logger(f"profilo.{token}")


def _sanitize_log_token(value: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    sanitized = sanitized.strip("_")
    return sanitized or "profilo"


def profile_display_name(env_file: str) -> str:
    """Restituisce un nome leggibile per il profilo."""
    normalized = os.path.normpath(env_file)
    env_dir = "aziende"
    if normalized == ".env":
        return ".env"
    if normalized.startswith(f"{env_dir}{os.sep}"):
        return normalized
    return os.path.basename(normalized)


def get_log_file_path(env_file: str) -> str:
    """Calcola il path del log file per un determinato file .env di profilo."""
    normalized = os.path.normpath(env_file)
    if normalized == ".env":
        return DEFAULT_LOG_FILE

    base_name = os.path.basename(normalized)
    if base_name == ".env":
        token = "env_default"
    elif base_name.startswith(".env."):
        token = base_name[len(".env."):]
    else:
        token = base_name.lstrip(".")

    token = _sanitize_log_token(token)
    return os.path.join(DEFAULT_LOG_DIR, f"log_{token}.txt")


# ─── Inizializzazione predefinita ──────────────────────────────────────────────
_log = get_logger("app")
_log.info(
    "Sistema di logging inizializzato (max %d MB per file, %d backup)",
    MAX_LOG_SIZE // 1024 // 1024,
    BACKUP_COUNT,
)

__all__ = [
    "get_logger",
    "set_active_log_file",
    "get_log_path",
    "create_profile_logger",
    "profile_display_name",
    "get_log_file_path",
]
