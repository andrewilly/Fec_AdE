"""
Modelli ORM SQLAlchemy e gestione della connessione al database.

Supporta SQLite (default) e MySQL via pymysql.
La configurazione DB viene letta da ConfigManager (env → file → variabili d'ambiente).
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, Text, text, inspect
from sqlalchemy.engine import URL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from app.config import config

# ─── Modelli ORM ────────────────────────────────────────────────────────────────

Base = declarative_base()


class Anagrafica(Base):
    __tablename__ = 'anagrafica'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fiscale = Column(String(255), unique=True, index=True)
    piva = Column(String(255))
    cf = Column(String(255))
    denominazione = Column(String(255))
    indirizzo = Column(String(255))
    comune = Column(String(255))
    cap = Column(String(255))
    nazione = Column(String(255))


class DatiGenerali(Base):
    __tablename__ = 'dati_generali'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome_file = Column(String(255), unique=True)
    tipo_documento = Column(String(255))
    divisa = Column(String(255))
    data = Column(String(255))
    numero = Column(String(255))
    data_ricezione = Column(String(255))
    importo_totale = Column(Float)
    arrotondamento = Column(Float)
    causale = Column(Text)
    transfrontaliera = Column(Integer, default=0)
    paese_cedente = Column(String(2))
    paese_cessionario = Column(String(2))
    id_cedente = Column(Integer, ForeignKey('anagrafica.id'))
    id_cessionario = Column(Integer, ForeignKey('anagrafica.id'))

    cedente = relationship("Anagrafica", foreign_keys=[id_cedente])
    cessionario = relationship("Anagrafica", foreign_keys=[id_cessionario])
    righe = relationship("RigheFattura", back_populates="fattura")
    riferimenti = relationship("DatiRiferimento", back_populates="fattura")
    ddt = relationship("DatiDDT", back_populates="fattura")
    riepilogo = relationship("DatiRiepilogo", back_populates="fattura")
    pagamenti = relationship("DatiPagamento", back_populates="fattura")


class RigheFattura(Base):
    __tablename__ = 'righe_fattura'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fattura = Column(Integer, ForeignKey('dati_generali.id'))
    numero_linea = Column(Integer)
    descrizione = Column(Text)
    quantita = Column(Float)
    prezzo_unitario = Column(Float)
    prezzo_totale = Column(Float)
    aliquota_iva = Column(Float)
    fattura = relationship("DatiGenerali", back_populates="righe")


class DatiRiferimento(Base):
    __tablename__ = 'dati_riferimento'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fattura = Column(Integer, ForeignKey('dati_generali.id'))
    tipo = Column(String(255))
    riferimento_numero_linea = Column(Integer)
    id_documento = Column(String(255))
    data = Column(String(255))
    codice_commessa = Column(String(255))
    codice_cup = Column(String(255))
    codice_cig = Column(String(255))
    id_riga_db = Column(Integer, ForeignKey('righe_fattura.id'), nullable=True)
    fattura = relationship("DatiGenerali", back_populates="riferimenti")


class DatiDDT(Base):
    __tablename__ = 'dati_ddt'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fattura = Column(Integer, ForeignKey('dati_generali.id'))
    numero_ddt = Column(String(255))
    data_ddt = Column(String(255))
    riferimento_numero_linea = Column(Integer)
    id_riga_db = Column(Integer, ForeignKey('righe_fattura.id'), nullable=True)
    fattura = relationship("DatiGenerali", back_populates="ddt")


class DatiRiepilogo(Base):
    __tablename__ = 'dati_riepilogo'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fattura = Column(Integer, ForeignKey('dati_generali.id'))
    aliquota_iva = Column(Float)
    natura = Column(String(255))
    spese_accessorie = Column(Float)
    arrotondamento = Column(Float)
    imponibile_importo = Column(Float)
    imposta = Column(Float)
    esigibilita_iva = Column(String(255))
    riferimento_normativo = Column(String(255))
    fattura = relationship("DatiGenerali", back_populates="riepilogo")


class DatiPagamento(Base):
    __tablename__ = 'dati_pagamento'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_fattura = Column(Integer, ForeignKey('dati_generali.id'))
    condizioni_pagamento = Column(String(255))
    fattura = relationship("DatiGenerali", back_populates="pagamenti")
    dettagli = relationship("DettaglioPagamento", back_populates="testata_pagamento")


class DettaglioPagamento(Base):
    __tablename__ = 'dettaglio_pagamento'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_pagamento = Column(Integer, ForeignKey('dati_pagamento.id'))
    modalita_pagamento = Column(String(255))
    data_scadenza = Column(String(255))
    importo = Column(Float)
    iban = Column(String(255))
    abi = Column(String(255))
    cab = Column(String(255))
    bic = Column(String(255))
    testata_pagamento = relationship("DatiPagamento", back_populates="dettagli")


class Corrispettivi(Base):
    __tablename__ = 'corrispettivi'
    id = Column(Integer, primary_key=True, autoincrement=True)
    piva = Column(String(255), index=True)
    tipo_corrispettivo = Column(String(10))
    data_ora_rilevazione = Column(String(50))
    imponibile_vendite = Column(Float)
    imposta_vendite = Column(Float)
    id_invio = Column(String(50), index=True)
    matricola = Column(String(100))
    importato_il = Column(String(50))


# ─── Gestione connessione ───────────────────────────────────────────────────────

engine = None
SessionLocal = None
CURRENT_DB_TYPE = "sqlite"
CURRENT_DB_ENV_FILE = ".env"
CURRENT_CONNECTION_STRING = ""
CURRENT_DISPLAY_CONNECTION_STRING = ""


def build_connection_target(env: dict):
    """Costruisce il target di connessione dal dict di configurazione."""
    db_type = env.get("DB_TYPE", "sqlite").strip().lower() or "sqlite"

    if db_type == "mysql":
        db_host = env.get("DB_HOST", "localhost")
        db_port = env.get("DB_PORT", "3306")
        db_name = env.get("DB_NAME", "")
        db_user = env.get("DB_USER", "")
        db_pass = env.get("DB_PASS", "")
        url = URL.create(
            "mysql+pymysql",
            username=db_user,
            password=db_pass,
            host=db_host,
            port=int(db_port) if str(db_port).strip() else 3306,
            database=db_name,
        )
        return db_type, url

    db_path = env.get("DB_SQLITE_PATH", "output/fatture_v3.db")
    return "sqlite", f"sqlite:///{db_path}"


def configure_database(env_path: str = ".env", env: dict = None) -> str:
    """
    Configura la connessione al database.
    Se env è None, carica la configurazione da config (già popolato da ConfigManager).
    """
    global engine, SessionLocal, CURRENT_DB_TYPE, CURRENT_DB_ENV_FILE
    global CURRENT_CONNECTION_STRING, CURRENT_DISPLAY_CONNECTION_STRING

    if env is None:
        env = config.to_dict()

    db_type, connection_target = build_connection_target(env)
    if isinstance(connection_target, URL):
        full_connection_string = connection_target.render_as_string(hide_password=False)
        display_connection_string = connection_target.render_as_string()
    else:
        full_connection_string = connection_target
        display_connection_string = connection_target

    if engine is not None and full_connection_string == CURRENT_CONNECTION_STRING:
        CURRENT_DB_TYPE = db_type
        CURRENT_DB_ENV_FILE = env_path
        return display_connection_string

    if engine is not None:
        engine.dispose()

    engine = create_engine(connection_target)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    CURRENT_DB_TYPE = db_type
    CURRENT_DB_ENV_FILE = env_path
    CURRENT_CONNECTION_STRING = full_connection_string
    CURRENT_DISPLAY_CONNECTION_STRING = display_connection_string
    return display_connection_string


def get_engine():
    global engine  # noqa: F824
    if engine is None:
        configure_database()
    return engine


def get_session_factory():
    global SessionLocal  # noqa: F824
    if SessionLocal is None:
        configure_database()
    return SessionLocal


def get_database_info() -> dict:
    return {
        "db_type": CURRENT_DB_TYPE,
        "env_file": CURRENT_DB_ENV_FILE,
        "connection_string": CURRENT_DISPLAY_CONNECTION_STRING,
    }


def init_db():
    """
    Inizializza il database: crea tabelle se non esistono,
    e aggiunge colonne mancanti (migrazione leggera).
    """
    current_engine = get_engine()
    Base.metadata.create_all(bind=current_engine)
    inspector = inspect(current_engine)
    columns = [c['name'] for c in inspector.get_columns('dati_generali')]
    new_columns = {
        "data_ricezione":    "TEXT",
        "transfrontaliera":  "INTEGER DEFAULT 0",
        "paese_cedente":     "VARCHAR(2)",
        "paese_cessionario": "VARCHAR(2)",
    }
    missing = [(col, ddl) for col, ddl in new_columns.items() if col not in columns]
    if missing:
        with current_engine.connect() as conn:
            for col, ddl in missing:
                conn.execute(text(f"ALTER TABLE dati_generali ADD COLUMN {col} {ddl}"))
            conn.commit()
