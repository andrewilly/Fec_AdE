"""
Recupero delle deleghe attive dall'Agenzia delle Entrate.

UTENZA=3 (Incaricato):
    Usa il wizard API per ottenere TUTTI i clienti disponibili.
    Già funzionante, estratto da menu.py e reso condivisibile.

UTENZA=2 (Delega Diretta):
    Il wizard API supporta anche ``tipoutenza="delegaDiretta"`` per ottenere
    l'elenco delle deleghe dirette (clienti che hanno delegato l'utente
    autenticato a operare per loro conto).  Questo file implementa:

      ``fetch_deleghe_dirette_from_wizard()`` — chiama il wizard con
      ``tipoutenza="delegaDiretta"`` e ``cf=""`` per ottenere TUTTE le
      posizioni in delega diretta.

    Se la chiamata API fallisce (ad esempio perché l'utente non ha deleghe
    dirette o il wizard non supporta la modalità), viene usato il file CSV
    di fallback salvato in ``~/.fec_ade/deleghe.csv``.

    ``fetch_all_deleghe()`` — unisce i risultati di entrambe le modalità
    (Incaricato + Delega Diretta) in un'unica lista, aggiungendo il campo
    ``tipo_delega`` ("INCARICATO" / "DELEGA_DIRETTA").
"""

import csv
import io
import json
import os
from datetime import datetime, date
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import HOME_CONFIG_DIR

# ─── Costanti ───────────────────────────────────────────────────────────────────
DEFAULT_CSV_DELEGHE = os.path.join(HOME_CONFIG_DIR, "deleghe.csv")
RAGIONI_SOCIALI_FILE = os.path.join(HOME_CONFIG_DIR, "ragioni_sociali.json")

# Wizard URL
WIZARD_TIPOUTENZA_INCARICATO = "incaricato"
WIZARD_TIPOUTENZA_DELEGA_DIRETTA = "delegaDiretta"
WIZARD_TIPODELEGA_DIRETTA = "delDiretta"
WIZARD_URL = "https://ivaservizi.agenziaentrate.gov.it/instr/instradamento-fatture-rest/rs/procediWizard"

# Etichette per il campo tipo_delega
TIPO_DELEGA_INCARICATO = "INCARICATO"
TIPO_DELEGA_DIRETTA = "DELEGA_DIRETTA"


# ═══════════════════════════════════════════════════════════════════════════════
# UTENZA=3 — Wizard API (Incaricato)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_ragione_sociale(incaricante: Dict) -> str:
    denominazione = str(incaricante.get("denominazione", "")).strip()
    if denominazione:
        return denominazione
    nome = str(incaricante.get("nome", "")).strip()
    cognome = str(incaricante.get("cognome", "")).strip()
    if nome or cognome:
        return f"{nome} {cognome}".strip()
    return ""


def _carica_mappa_ragioni_sociali() -> Dict[str, str]:
    if os.path.exists(RAGIONI_SOCIALI_FILE):
        with open(RAGIONI_SOCIALI_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _salva_mappa_ragioni_sociali(mappa: Dict[str, str]):
    os.makedirs(HOME_CONFIG_DIR, exist_ok=True)
    with open(RAGIONI_SOCIALI_FILE, "w", encoding="utf-8") as f:
        json.dump(mappa, f, ensure_ascii=False, indent=2)


def _extract_incarichi(template: Dict) -> List[Dict]:
    """Estrae la lista incarichi dal template wizard."""
    candidates: List[Dict] = []
    roots: List[Optional[Dict]] = [template]
    nested = template.get("template")
    if isinstance(nested, dict):
        roots.append(nested)

    for root in roots:
        if not isinstance(root, dict):
            continue
        richiesta = root.get("richiestaIncarichi")
        if not isinstance(richiesta, dict):
            continue
        incarichi = richiesta.get("incarichi")
        if isinstance(incarichi, list):
            candidates.extend(item for item in incarichi if isinstance(item, dict))

    return candidates


def fetch_clients_from_wizard(
    request_with_x_appl_func: Callable[..., Any],
    logger_func: Callable[..., None],
) -> List[Dict]:
    """
    Recupera l'elenco dei clienti (incarichi) dal wizard dell'Agenzia Entrate.
    Restituisce una lista di dict con: cf, sede, tipo, piva, ragione_sociale.

    Richiede che la sessione sia già autenticata (login già effettuato).
    """
    logger_func("Recupero elenco clienti dal wizard...")

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
    payload = {"tipoutenza": WIZARD_TIPOUTENZA_INCARICATO, "cf": ""}

    r = request_with_x_appl_func("POST", WIZARD_URL, json=payload, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"Wizard non disponibile (status {r.status_code})")

    data = r.json()

    incarichi = _extract_incarichi(data)
    if not incarichi:
        logger_func("Nessun cliente trovato dal wizard.")
        return []

    mappa_rs = _carica_mappa_ragioni_sociali()
    mappa_modificata = False

    clienti = []
    for inc in incarichi:
        incaricante = inc.get("incaricante", {})
        cf = str(incaricante.get("cf", "")).strip()
        sede = str(incaricante.get("sede", "")).strip()
        if not cf:
            continue

        ragione_sociale = _get_ragione_sociale(incaricante)
        if not ragione_sociale:
            ragione_sociale = mappa_rs.get(cf, "")

        # Persisti la ragione sociale appena scoperta
        if ragione_sociale and cf not in mappa_rs:
            mappa_rs[cf] = ragione_sociale
            mappa_modificata = True

        label = cf
        tipo = sede if sede else "FOL"
        is_ent = "-" in sede if sede else False
        piva_cf = str(inc.get("pIva", "")).strip() or cf

        data_fine = str(inc.get("dataFine", "")).strip()

        clienti.append({
            "cf": cf,
            "sede": sede,
            "tipo": "ENT" if is_ent else "FOL",
            "label": f"{label} ({tipo})",
            "piva": piva_cf,
            "ragione_sociale": ragione_sociale,
            "data_fine": data_fine,
        })

    if mappa_modificata:
        _salva_mappa_ragioni_sociali(mappa_rs)
        logger_func(f"Mappa ragioni sociali aggiornata ({len(mappa_rs)} totali).")

    logger_func(f"Wizard: trovati {len(clienti)} clienti.")
    return clienti


# ═══════════════════════════════════════════════════════════════════════════════
# UTENZA=2 — Wizard API (Delega Diretta)
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_deleganti(template: Dict) -> List[Dict]:
    """
    Estrae la lista dei deleganti dal template wizard per tipoutenza
    ``delegaDiretta``.

    La struttura della risposta è analoga a ``_extract_incarichi()``:
    cerca ``richiestaIncarichi.incarichi[]`` sia in root che in
    ``template`` annidato.  Ogni incarico rappresenta un delegante.
    """
    candidates: List[Dict] = []
    roots: List[Optional[Dict]] = [template]
    nested = template.get("template")
    if isinstance(nested, dict):
        roots.append(nested)

    for root in roots:
        if not isinstance(root, dict):
            continue
        richiesta = root.get("richiestaIncarichi")
        if not isinstance(richiesta, dict):
            continue
        incarichi = richiesta.get("incarichi")
        if isinstance(incarichi, list):
            candidates.extend(item for item in incarichi if isinstance(item, dict))

    return candidates


def fetch_deleghe_dirette_from_wizard(
    wizard_proceed_func: Callable[..., Any],
    logger_func: Callable[..., None],
) -> List[Dict]:
    """
    Recupera l'elenco delle deleghe dirette dal wizard dell'Agenzia Entrate.

    Chiama ``procediWizard`` con ``tipoutenza="delegaDiretta"`` e
    ``cf=""``; se il wizard restituisce incarichi (deleganti) li
    trasforma in una lista con lo stesso formato di
    ``fetch_clients_from_wizard()``, aggiungendo il campo
    ``tipo_delega="DELEGA_DIRETTA"``.

    Restituisce lista vuota se il wizard non ha deleghe dirette o la
    risposta non è interpretabile.
    """
    logger_func("Recupero elenco deleghe dirette dal wizard...")

    payload = {
        "tipoutenza": WIZARD_TIPOUTENZA_DELEGA_DIRETTA,
        "cf": "",
    }

    try:
        data = wizard_proceed_func(payload)
    except Exception as exc:
        logger_func(f"Wizard delega diretta non disponibile: {exc}")
        return []

    if not isinstance(data, dict):
        logger_func("Risposta wizard delega diretta non valida (non dict).")
        return []

    incarichi = _extract_deleganti(data)
    if not incarichi:
        logger_func("Nessuna delega diretta trovata dal wizard.")
        return []

    mappa_rs = _carica_mappa_ragioni_sociali()

    deleghe = []
    for inc in incarichi:
        incaricante = inc.get("incaricante", {})
        cf = str(incaricante.get("cf", "")).strip()
        sede = str(incaricante.get("sede", "")).strip()
        if not cf:
            continue

        ragione_sociale = _get_ragione_sociale(incaricante)
        if not ragione_sociale:
            ragione_sociale = mappa_rs.get(cf, "")

        label = cf
        tipo = sede if sede else "FOL"
        is_ent = "-" in sede if sede else False
        piva_cf = str(inc.get("pIva", "")).strip() or cf

        data_fine = str(inc.get("dataFine", "")).strip()

        deleghe.append({
            "cf": cf,
            "sede": sede,
            "tipo": "ENT" if is_ent else "FOL",
            "label": f"{label} ({tipo})",
            "piva": piva_cf,
            "ragione_sociale": ragione_sociale,
            "tipo_delega": TIPO_DELEGA_DIRETTA,
            "data_fine": data_fine,
        })

    logger_func(f"Wizard (delega diretta): trovate {len(deleghe)} deleghe.")
    return deleghe


def fetch_all_deleghe(
    request_with_x_appl_func: Callable[..., Any],
    wizard_proceed_func: Optional[Callable[..., Any]] = None,
    logger_func: Optional[Callable[..., None]] = None,
    csv_path: str = DEFAULT_CSV_DELEGHE,
) -> List[Dict]:
    """
    Recupera l'elenco COMPLETO di tutte le deleghe (Incaricato + Delega
    Diretta) per l'utente autenticato.

    Strategia:

    1. **Incaricato** — chiamata esistente
       ``fetch_clients_from_wizard()`` con ``tipoutenza="incaricato"``,
       assegna ``tipo_delega="INCARICATO"``.

    2. **Delega Diretta via API** — se ``wizard_proceed_func`` è fornito,
       chiama ``fetch_deleghe_dirette_from_wizard()`` con
       ``tipoutenza="delegaDiretta"``.

    3. **Fallback CSV** — se la chiamata API per Delega Diretta fallisce
       o non è disponibile, carica le deleghe dal file CSV.

    I risultati vengono uniti in un'unica lista; eventuali duplicati
    (stesso CF in entrambe le modalità) vengono deduplicati tenendo il
    primo occorrenza.

    Args:
        request_with_x_appl_func: funzione HTTP autenticata (per incaricato).
        wizard_proceed_func: funzione ``_wizard_proceed`` del engine
            (per delega diretta via API).  Se ``None``, salta il tentativo
            API e usa solo CSV.
        logger_func: funzione di log.
        csv_path: path al file CSV di fallback.

    Returns:
        Lista di dict con almeno: cf, piva, ragione_sociale, tipo, sede,
        tipo_delega ("INCARICATO" o "DELEGA_DIRETTA").
    """
    if logger_func is None:
        from app.log_config import get_logger
        _log = get_logger("deleghe")
        logger_func = _log.info

    # ── 1. Incaricato ──────────────────────────────────────────────────────
    incaricati = fetch_clients_from_wizard(request_with_x_appl_func, logger_func)
    for cli in incaricati:
        cli["tipo_delega"] = TIPO_DELEGA_INCARICATO

    # ── 2. Delega Diretta ──────────────────────────────────────────────────
    dirette: List[Dict] = []

    if wizard_proceed_func is not None:
        try:
            dirette = fetch_deleghe_dirette_from_wizard(wizard_proceed_func, logger_func)
        except Exception as exc:
            logger_func(f"API delega diretta non disponibile: {exc}")
            dirette = []

    # Fallback CSV se API non ha prodotto risultati
    if not dirette:
        logger_func("Nessuna delega diretta via API — provo fallback CSV...")
        csv_deleghe = load_deleghe_from_csv(csv_path)
        for d in csv_deleghe:
            d["tipo_delega"] = TIPO_DELEGA_DIRETTA
            if "sede" not in d:
                d["sede"] = ""
            if "piva" not in d:
                d["piva"] = d.get("cf", "")
        dirette = csv_deleghe
        if dirette:
            logger_func(f"Caricate {len(dirette)} deleghe dirette da CSV.")
    else:
        logger_func(f"Trovate {len(dirette)} deleghe dirette via API.")

    # ── 3. Merge & deduplica ───────────────────────────────────────────────
    seen_cf: set = set()
    tutte: List[Dict] = []

    for d in incaricati + dirette:
        cf = d.get("cf", "")
        if cf in seen_cf:
            continue
        seen_cf.add(cf)
        # Garantisce che il campo tipo_delega sia sempre presente
        if "tipo_delega" not in d:
            d["tipo_delega"] = TIPO_DELEGA_INCARICATO
        tutte.append(d)

    logger_func(f"Totale deleghe (unificate): {len(tutte)}.")
    return tutte


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT PROBE — Ricerca API deleghe dirette sul portale
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Definizione endpoint candidati ─────────────────────────────────────────────
# Ogni endpoint è un dict con:
#   method: "GET" o "POST"
#   url: URL (con ?v= dove serve)
#   json_body: dict opzionale per POST (se None, usa {"cf": ""})
#   min_cf: minimo CF da restituire per considerare l'endpoint valido
#           (2+ per evitare di fermarsi sul profilo dell'intermediario)
#   priority: più alto = testato prima (100 max)
#
# Endpoint REALI scoperti da reverse engineering di RecuFatture.exe.

EndpointDef = Dict[str, Any]

CANDIDATE_ENDPOINTS: List[EndpointDef] = [
    # ═══════════════════════════════════════════════════════════════════════════
    # apptel.agenziaentrate.gov.it — ENDPOINT REALI (RecuFatture)
    # ═══════════════════════════════════════════════════════════════════════════

    # deleganti: lista di chi ha delegato l'utente (il più promettente)
    # Probabilmente POST con CF dell'intermediario
    {"method": "POST", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/delegheUniche/deleganti",
     "json_body": {"cf": ""}, "min_cf": 2, "priority": 100},
    {"method": "POST", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/delegheUniche/deleganti",
     "json_body": {}, "min_cf": 2, "priority": 99},
    # serviziRicerca: ricerca servizi delegati
    {"method": "POST", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/delegheUniche/serviziRicerca",
     "json_body": {"cf": ""}, "min_cf": 2, "priority": 90},
    # adesione: adesioni a servizi
    {"method": "GET", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/delegheUniche/adesione?v=",
     "json_body": None, "min_cf": 2, "priority": 80},
    # utenteLavoro: profilo utente (restituisce 1 CF = l'intermediario)
    {"method": "GET", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/deleghe/utenteLavoro?v=",
     "json_body": None, "min_cf": 2, "priority": 10},
    {"method": "POST", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/deleghe/utenteLavoro",
     "json_body": {"cf": ""}, "min_cf": 2, "priority": 5},
    # contatori: statistiche (pochi CF)
    {"method": "GET", "url": "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/delegheUniche/reperimentoContatoriDeleghe?v=",
     "json_body": None, "min_cf": 2, "priority": 1},

    # ═══════════════════════════════════════════════════════════════════════════
    # Portale REST — endpoint ipotizzati
    # ═══════════════════════════════════════════════════════════════════════════
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/ricevute",
     "json_body": None, "min_cf": 2, "priority": 50},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/deleganti",
     "json_body": None, "min_cf": 2, "priority": 49},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/elenco",
     "json_body": None, "min_cf": 2, "priority": 40},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/mieDeleghe",
     "json_body": None, "min_cf": 2, "priority": 39},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/delegheRicevute",
     "json_body": None, "min_cf": 2, "priority": 38},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/ricerca",
     "json_body": None, "min_cf": 2, "priority": 37},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/servizi/deleghe",
     "json_body": None, "min_cf": 2, "priority": 36},
    # CSV export
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/esportaElenco",
     "json_body": None, "min_cf": 2, "priority": 30},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/esporta",
     "json_body": None, "min_cf": 2, "priority": 29},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/export",
     "json_body": None, "min_cf": 2, "priority": 28},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/csv",
     "json_body": None, "min_cf": 2, "priority": 27},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/download",
     "json_body": None, "min_cf": 2, "priority": 26},
    # Portale con query
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/ricevute?tipo=DIRETTA",
     "json_body": None, "min_cf": 2, "priority": 20},
    {"method": "GET", "url": "https://portale.agenziaentrate.gov.it/portale-rest/rs/deleghe/ricevute?tipo=TUTTE",
     "json_body": None, "min_cf": 2, "priority": 19},

    # ═══════════════════════════════════════════════════════════════════════════
    # Instradamento fatture
    # ═══════════════════════════════════════════════════════════════════════════
    {"method": "GET", "url": "https://ivaservizi.agenziaentrate.gov.it/instr/instradamento-fatture-rest/rs/deleghe/elenco",
     "json_body": None, "min_cf": 2, "priority": 15},
]


def _extract_cf_list_from_response(response: Any) -> List[Dict]:
    """
    Prova a estrarre una lista di CF (con denominazione) dalla risposta HTTP.

    Gestisce vari formati:
    - JSON strutturato (dict con lista in ``deleghe``, ``deleganti``, ecc.)
    - JSON array (lista diretta di dict)
    - CSV con ``;`` come delimitatore
    """
    text = ""
    if hasattr(response, "text"):
        text = response.text or ""
    elif isinstance(response, str):
        text = response
    else:
        return []

    # ── Prova JSON da response.json() (funziona anche con text vuoto) ──
    if hasattr(response, "json"):
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = None
        if data is not None:
            result = _extract_cf_from_json(data)
            if result:
                return result

    # Se non c'è testo, non possiamo fare altro
    if not text.strip():
        return []

    content_type = ""
    if hasattr(response, "headers") and response.headers:
        content_type = (response.headers.get("Content-Type") or "").lower()

    # ── Prova JSON da text (fallback) ──
    if text.strip().startswith("{") or text.strip().startswith("["):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if data is not None:
            result = _extract_cf_from_json(data)
            if result:
                return result

    # ── Prova CSV ──
    if "csv" in content_type or "csv" in text[:200].lower():
        result = _extract_cf_from_csv_text(text)
        if result:
            return result

    # ── Fallback CSV ──
    result = _extract_cf_from_csv_text(text)
    if result:
        return result

    return []


def _extract_cf_from_json(data: Any) -> List[Dict]:
    """Estrae CF da una struttura JSON arbitraria."""
    # Chiavi possibili per il CF
    cf_keys = (
        "cf", "codiceFiscale", "CODICE_FISCALE", "CF", "cF", "CodiceFiscale",
        "cfDelegante", "cfDelegato",
    )
    # Chiavi possibili per la denominazione
    name_keys = (
        "denominazione", "ragioneSociale", "RAGIONE_SOCIALE",
        "DENOMINAZIONE", "nome", "cognome", "ragione_sociale",
        "denomDelegante", "denomDelegato", "denominazioneDelegante",
    )
    # Chiavi contenitore che possono racchiudere una lista
    container_keys = (
        "deleghe", "deleganti", "elenco", "lista", "incarichi",
        "items", "data", "result", "records", "listaDeleghe",
    )

    # Chiavi possibili per la sede
    sede_keys = ("sedeDelegante", "sedeDelegato", "sede", "sedeDeleganteCf")

    def _extract_from_item(item: Dict) -> Optional[Dict]:
        cf = None
        for key in cf_keys:
            val = item.get(key)
            if val and isinstance(val, str) and val.strip():
                cf = val.strip()
                break
        if not cf:
            return None

        denominazione = ""
        for key in name_keys:
            val = item.get(key)
            if val and isinstance(val, str) and val.strip():
                denominazione = val.strip()
                break

        sede = ""
        for key in sede_keys:
            val = item.get(key)
            if val and isinstance(val, str) and val.strip():
                sede = val.strip()
                break

        return {"cf": cf, "ragione_sociale": denominazione, "sede": sede}

    # Se è già una lista, estrae direttamente
    if isinstance(data, list):
        result = []
        for item in data:
            if isinstance(item, dict):
                entry = _extract_from_item(item)
                if entry:
                    result.append(entry)
        if result:
            return result

    # Se è un dict, cerca array nei container keys
    if isinstance(data, dict):
        for container in container_keys:
            arr = data.get(container)
            if isinstance(arr, list):
                result = []
                for item in arr:
                    if isinstance(item, dict):
                        entry = _extract_from_item(item)
                        if entry:
                            result.append(entry)
                if result:
                    return result

        # Prova a leggere direttamente le chiavi del dict
        entry = _extract_from_item(data)
        if entry:
            return [entry]

    return []


def _extract_cf_from_csv_text(text: str) -> List[Dict]:
    """Estrae CF da un testo in formato CSV con delimitatore ``;``."""
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        cf_key = None
        for candidate in ("CF", "CODICE_FISCALE", "CodiceFiscale", "cf"):
            if candidate in (reader.fieldnames or []):
                cf_key = candidate
                break
        if not cf_key:
            return []

        result = []
        for row in reader:
            cf = (row.get(cf_key) or "").strip()
            if not cf:
                continue
            denominazione = (
                row.get("Denominazione")
                or row.get("DENOMINAZIONE")
                or row.get("RagioneSociale")
                or row.get("ragione_sociale")
                or ""
            ).strip()
            result.append({"cf": cf, "ragione_sociale": denominazione})
        return result
    except Exception:
        return []


def _try_endpoint(
    request_func: Callable[..., Any],
    session: Any,
    endpoint: EndpointDef,
    logger_func: Callable[..., None],
) -> List[Dict]:
    """
    Prova un endpoint (GET o POST) e restituisce i CF trovati.

    Rispetta ``min_cf``: se l'endpoint restituisce meno CF della soglia,
    viene considerato non valido e la ricerca continua.

    Args:
        request_func: funzione HTTP con header x-appl.
        session: sessione requests per tentativi senza x-appl.
        endpoint: dict con method, url, json_body, min_cf.
        logger_func: funzione di log.

    Returns:
        Lista di dict (cf, ragione_sociale) trovati, oppure lista vuota.
    """
    method = endpoint["method"]
    url = endpoint["url"]
    json_body = endpoint.get("json_body")
    min_cf = endpoint.get("min_cf", 2)

    # Costruisce kwargs per la richiesta
    kwargs: Dict[str, Any] = {}
    if method == "POST" and json_body is not None:
        kwargs["json"] = json_body

    # ── Tentativo CON x-appl ──
    try:
        r = request_func(method, url, **kwargs)
        status = r.status_code
        body_preview = (r.text or "")[:300]
        logger_func(f"  [X-APPL {status}] {method} {url}")
        if body_preview:
            logger_func(f"    Body: {body_preview[:200]}")

        if status == 200:
            cf_list = _extract_cf_list_from_response(r)
            if len(cf_list) >= min_cf:
                logger_func(
                    f"    >>> Trovati {len(cf_list)} CF! "
                    f"(primi: {[d['cf'] for d in cf_list[:3]]})"
                )
                return cf_list
            elif cf_list:
                logger_func(
                    f"    >>> Trovati solo {len(cf_list)} CF "
                    f"(min_cf={min_cf}), continuo..."
                )
    except Exception as e:
        logger_func(f"  [X-APPL ERR] {method} {url}: {e}")

    # ── Tentativo SENZA x-appl (sessione diretta) ──
    if session is not None:
        try:
            kwargs2: Dict[str, Any] = {}
            if method == "POST" and json_body is not None:
                kwargs2["json"] = json_body
            r2 = session.request(method, url, timeout=30, **kwargs2)
            status2 = r2.status_code
            body_preview2 = (r2.text or "")[:300]
            logger_func(f"  [DIRECT {status2}] {method} {url}")
            if body_preview2:
                logger_func(f"    Body: {body_preview2[:200]}")

            if status2 == 200:
                cf_list = _extract_cf_list_from_response(r2)
                if len(cf_list) >= min_cf:
                    logger_func(
                        f"    >>> Trovati {len(cf_list)} CF! "
                        f"(primi: {[d['cf'] for d in cf_list[:3]]})"
                    )
                    return cf_list
                elif cf_list:
                    logger_func(
                        f"    >>> Trovati solo {len(cf_list)} CF "
                        f"(min_cf={min_cf}), continuo..."
                    )
        except Exception as e:
            logger_func(f"  [DIRECT ERR] {method} {url}: {e}")

    return []


def _debug_probe_endpoints(
    request_with_x_appl_func: Callable[..., Any],
    session: Any,
    logger_func: Callable[..., None],
) -> List[Dict]:
    """
    Prova TUTTI gli endpoint candidati e logga i risultati dettagliati.

    Per ogni endpoint mostra: metodo, URL, status code, primi 300 caratteri
    del body, numero di CF trovati e se hanno superato la soglia ``min_cf``.

    Gli endpoint sono ordinati per priorità (più alto = prima).

    Args:
        request_with_x_appl_func: funzione HTTP con header x-appl.
        session: sessione requests per tentativi senza x-appl.
        logger_func: funzione di log.

    Returns:
        Lista di dict (cf, ragione_sociale) trovati, oppure lista vuota.
    """
    logger_func("=" * 60)
    logger_func("DEBUG PROBE: Verifica endpoint deleghe dirette...")
    logger_func("=" * 60)

    # Ordina per priorità decrescente
    sorted_endpoints = sorted(
        CANDIDATE_ENDPOINTS, key=lambda e: e.get("priority", 0), reverse=True
    )

    best_result: List[Dict] = []
    found_endpoint = ""

    for endpoint in sorted_endpoints:
        cf_list = _try_endpoint(
            request_with_x_appl_func, session, endpoint, logger_func
        )
        if cf_list and not best_result:
            best_result = cf_list
            found_endpoint = f"{endpoint['method']} {endpoint['url']}"

    logger_func("=" * 60)
    if best_result:
        logger_func(
            f"DEBUG PROBE: Endpoint FUNZIONANTE: {found_endpoint} "
            f"({len(best_result)} CF)"
        )
    else:
        logger_func("DEBUG PROBE: Nessun endpoint funzionante trovato.")
        logger_func(
            "DEBUG PROBE: Verifica manuale: apri il browser nell'area "
            "riservata AE → Profilo → Deleghe → Chi mi ha delegato, "
            "apri la Console Rete e cerca chiamate API."
        )
    logger_func("=" * 60)

    return best_result


# ═══════════════════════════════════════════════════════════════════════════════
# fetch_all_deleghe_enhanced — Versione avanzata con probing endpoint
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_all_deleghe_enhanced(
    engine: Any,
    logger_func: Optional[Callable[..., None]] = None,
    csv_path: str = DEFAULT_CSV_DELEGHE,
    debug: bool = False,
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Versione avanzata di ``fetch_all_deleghe()`` che usa il probing degli
    endpoint del portale per trovare l'API corretta per le deleghe dirette.

    Strategia:

    1. **Step 1 – Incaricato**: chiamata wizard con
       ``tipoutenza="incaricato"`` (identica alla versione base).

    2. **Step 2 – Probe API**: se ``debug=True``, attiva il probing
       dettagliato di tutti gli endpoint candidati (con e senza header
       x-appl).  Altrimenti, prova gli endpoint in sequenza finché uno
       non risponde 200 e restituisce CF estraibili.

    3. **Step 3 – Fallback CSV**: se nessun endpoint ha funzionato,
       carica le deleghe dirette dal file CSV (``~/.fec_ade/deleghe.csv``).

    4. **Step 4 – Merge & deduplica**: unisce incaricato + dirette per
       CF, tenendo la prima occorrenza (quindi se uno stesso CF compare
       in entrambi i tipi, prevale INCARICATO).

    Args:
        engine: Istanza di ``FEScraperEngine`` già autenticata (login
            già effettuato).  Vengono usati ``engine._request_with_x_appl``,
            ``engine.session`` e ``engine.logger``.
        logger_func: Funzione di logging (es. ``print`` o ``_log.info``).
        csv_path: Path al file CSV di fallback per deleghe dirette.
        debug: Se ``True``, attiva il probing debug di TUTTI gli endpoint.

    Returns:
        ``(lista_deleghe, dict_stat)`` dove:

        - ``lista_deleghe`` è una lista di dict con almeno: ``cf``,
          ``piva``, ``ragione_sociale``, ``tipo``, ``sede``,
          ``tipo_delega`` (``"INCARICATO"`` o ``"DELEGA_DIRETTA"``).
        - ``dict_stat`` è un dict con i conteggi: ``{"totale": N,
          "INCARICATO": N, "DELEGA_DIRETTA": N}``.
    """
    if logger_func is None:
        from app.log_config import get_logger
        _log = get_logger("deleghe")
        logger_func = _log.info

    request_func = getattr(engine, "_request_with_x_appl", None)
    session = getattr(engine, "session", None)

    if request_func is None:
        raise ValueError(
            "engine deve avere un metodo _request_with_x_appl "
            "(FEScraperEngine già autenticato)"
        )

    # ── Step 1: Incaricato ──────────────────────────────────────────────
    logger_func("Step 1/4: Recupero clienti incaricato dal wizard...")
    incaricati = fetch_clients_from_wizard(request_func, logger_func)
    for cli in incaricati:
        cli["tipo_delega"] = TIPO_DELEGA_INCARICATO
    logger_func(f"  \u2192 Trovati {len(incaricati)} clienti incaricato.")

    # ── Step 2: Probe endpoint per deleghe dirette ─────────────────────
    logger_func("Step 2/4: Ricerca API deleghe dirette...")
    dirette: List[Dict] = []

    if debug:
        logger_func("  DEBUG MODE: attivato probing dettagliato endpoint...")
        probe_result = _debug_probe_endpoints(request_func, session, logger_func)
        if probe_result:
            for d in probe_result:
                d["tipo_delega"] = TIPO_DELEGA_DIRETTA
            dirette = probe_result
            logger_func(f"  \u2192 Trovate {len(dirette)} deleghe dirette via probe.")
    else:
        # Prova endpoint candidati in sequenza (ordinati per priorità)
        sorted_endpoints = sorted(
            CANDIDATE_ENDPOINTS,
            key=lambda e: e.get("priority", 0),
            reverse=True,
        )
        for endpoint in sorted_endpoints:
            cf_list = _try_endpoint(
                request_func, session, endpoint, logger_func
            )
            if cf_list:
                logger_func(
                    f"  \u2192 Endpoint funzionante: {endpoint['method']} "
                    f"{endpoint['url']} ({len(cf_list)} CF)"
                )
                for d in cf_list:
                    d["tipo_delega"] = TIPO_DELEGA_DIRETTA
                dirette = cf_list
                break

    # ── Step 3: Fallback CSV ────────────────────────────────────────────
    if not dirette:
        logger_func("Step 3/4: Nessuna API deleghe dirette trovata.")
        logger_func("  Provo fallback CSV...")
        csv_deleghe = load_deleghe_from_csv(csv_path)
        if csv_deleghe:
            for d in csv_deleghe:
                d["tipo_delega"] = TIPO_DELEGA_DIRETTA
                if "sede" not in d:
                    d["sede"] = ""
                if "piva" not in d:
                    d["piva"] = d.get("cf", "")
            dirette = csv_deleghe
            logger_func(f"  \u2192 Caricate {len(dirette)} deleghe dirette da CSV.")
        else:
            logger_func("  \u2192 Nessuna delega diretta trovata (API e CSV vuoto).")
    else:
        logger_func(f"  \u2192 Trovate {len(dirette)} deleghe dirette.")

    # ── Step 4: Merge & deduplica ─────────────────────────────────────
    logger_func("Step 4/4: Unione e deduplica...")
    seen_cf: set = set()
    tutte: List[Dict] = []

    for d in incaricati + dirette:
        cf = d.get("cf", "")
        if not cf or cf in seen_cf:
            continue
        seen_cf.add(cf)
        if "tipo_delega" not in d:
            d["tipo_delega"] = TIPO_DELEGA_INCARICATO
        if "sede" not in d:
            d["sede"] = ""
        if "piva" not in d:
            d["piva"] = cf
        tutte.append(d)

    # Calcola statistiche
    inc_count = sum(
        1 for d in tutte if d.get("tipo_delega") == TIPO_DELEGA_INCARICATO
    )
    dir_count = sum(
        1 for d in tutte if d.get("tipo_delega") == TIPO_DELEGA_DIRETTA
    )

    logger_func(
        f"  \u2192 Totale: {len(tutte)} clienti "
        f"({inc_count} incaricato, {dir_count} delega diretta)."
    )

    return tutte, {
        "totale": len(tutte),
        TIPO_DELEGA_INCARICATO: inc_count,
        TIPO_DELEGA_DIRETTA: dir_count,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# UTENZA=2 — CSV sicuro (Delega Diretta)
# ═══════════════════════════════════════════════════════════════════════════════


def load_deleghe_from_csv(csv_path: str = DEFAULT_CSV_DELEGHE) -> List[Dict]:
    """
    Carica le deleghe da un file CSV (formato:
    CF;Servizio;Data_inizio;Data_fine oppure CF;Denominazione;...).

    Restituisce una lista di dict con almeno la chiave 'cf'.
    Se il file non esiste, restituisce lista vuota.
    """
    if not os.path.exists(csv_path):
        return []

    deleghe: List[Dict] = []
    mappa_rs = _carica_mappa_ragioni_sociali()
    mappa_modificata = False

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            cf = (row.get("CF") or "").strip()
            if not cf:
                continue
            denominazione_csv = row.get("Denominazione", "")
            rs = mappa_rs.get(cf, denominazione_csv)
            # Persisti se trovata nel CSV e non ancora in mappa
            if rs and cf not in mappa_rs:
                mappa_rs[cf] = rs
                mappa_modificata = True
            deleghe.append({
                "cf": cf,
                "tipo": "FOL",
                "ragione_sociale": rs,
            })

    if mappa_modificata:
        _salva_mappa_ragioni_sociali(mappa_rs)

    return deleghe


# ═══════════════════════════════════════════════════════════════════════════════
# Factory unificata
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_active_deleghe(
    utenza: str,
    request_with_x_appl_func: Optional[Callable[..., Any]] = None,
    logger_func: Optional[Callable[..., None]] = None,
    csv_path: str = DEFAULT_CSV_DELEGHE,
) -> List[Dict]:
    """
    Factory: restituisce la lista delle deleghe attive in base al tipo di utenza.

    Args:
        utenza: "3" per Incaricato (wizard API), "2" per Delega Diretta (CSV)
        request_with_x_appl_func: funzione HTTP (necessaria per UTENZA=3)
        logger_func: funzione di log
        csv_path: path al file CSV (per UTENZA=2)

    Returns:
        Lista di dict con almeno 'cf', 'tipo', 'ragione_sociale'
    """
    if logger_func is None:
        from app.log_config import get_logger
        _log = get_logger("deleghe")
        logger_func = _log.info

    if utenza == "3":
        if request_with_x_appl_func is None:
            raise ValueError("UTENZA=3 richiede request_with_x_appl_func (sessione autenticata)")
        return fetch_clients_from_wizard(request_with_x_appl_func, logger_func)

    if utenza == "2":
        deleghe = load_deleghe_from_csv(csv_path)
        if not deleghe:
            logger_func(
                f"Nessuna delega trovata in {csv_path}. "
                "Crea il file o scarica l'elenco dall'area riservata Agenzia Entrate."
            )
        else:
            logger_func(f"Caricate {len(deleghe)} deleghe da {csv_path}")
        return deleghe

    else:
        raise ValueError(f"Tipo utenza non supportato per recupero deleghe: {utenza}")


def salva_ragione_sociale(piva: str, cf: str, ragione_sociale: str):
    """Salva una ragione sociale nella mappa persistente."""
    mappa = _carica_mappa_ragioni_sociali()
    mappa[piva] = ragione_sociale
    if cf and cf != piva:
        mappa[cf] = ragione_sociale
    _salva_mappa_ragioni_sociali(mappa)


# ═══════════════════════════════════════════════════════════════════════════════
# Utilità: validità e scadenze
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def _carica_ultimo_json_deleghe() -> Optional[Dict]:
    """Carica il JSON più recente dalla directory output/."""
    if not os.path.isdir(OUTPUT_DIR):
        return None
    json_files = [
        f for f in os.listdir(OUTPUT_DIR)
        if f.startswith("deleghe_attive_") and f.endswith(".json")
    ]
    if not json_files:
        return None
    latest = max(json_files, key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)))
    path = os.path.join(OUTPUT_DIR, latest)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CONTATORI_URL = (
    "https://apptel.agenziaentrate.gov.it/deleghe-portale-rest/rs/"
    "delegheUniche/reperimentoContatoriDeleghe?v="
)


def check_delegations_validity(
    request_func: Callable[..., Any],
    logger_func: Optional[Callable[..., None]] = None,
) -> Dict:
    """
    Verifica rapida che le deleghe siano ancora attive.

    Usa l'endpoint ``reperimentoContatoriDeleghe`` per ottenere i conteggi
    correnti e li confronta con l'ultimo JSON salvato in ``output/``.

    Restituisce un dict con:
      - ``status``: "ok", "cambiato", "errore"
      - ``messaggio``: descrizione
      - ``contatori_correnti``: dict dei contatori (se disponibile)
      - ``conteggio_precedente``: dict con totale/incaricato/delega diretta
    """
    if logger_func is None:
        from app.log_config import get_logger
        _log = get_logger("deleghe")
        logger_func = _log.info

    risultato: Dict = {
        "status": "ok",
        "messaggio": "",
        "contatori_correnti": {},
        "conteggio_precedente": {},
    }

    # Carica ultimo JSON salvato
    precedente = _carica_ultimo_json_deleghe()
    if precedente:
        risultato["conteggio_precedente"] = {
            "totale": precedente.get("totale", 0),
            "data_estrazione": precedente.get("data_estrazione", ""),
        }

    # Chiamata API contatori
    try:
        r = request_func("GET", CONTATORI_URL)
        if r.status_code == 200:
            data = r.json()
            risultato["contatori_correnti"] = data
            # I contatori principali per le deleghe
            uniche = data.get("unicheDelegato", 0)
            if precedente and uniche != precedente.get("totale", 0):
                risultato["status"] = "cambiato"
                risultato["messaggio"] = (
                    f"Conteggio deleghe cambiato: {uniche} (erano "
                    f"{precedente.get('totale', 0)} all'ultimo controllo)"
                )
            else:
                risultato["messaggio"] = (
                    f"Conteggio deleghe invariato ({uniche} totali)."
                )
        else:
            risultato["status"] = "errore"
            risultato["messaggio"] = (
                f"Endpoint contatori non disponibile (HTTP {r.status_code})."
            )
    except Exception as exc:
        risultato["status"] = "errore"
        risultato["messaggio"] = f"Errore chiamata contatori: {exc}"

    return risultato


def generate_expiry_report(
    deleghe: List[Dict],
    soglia_giorni: int = 20,
) -> Dict:
    """
    Genera un report sullo stato di scadenza delle deleghe.

    Args:
        deleghe: lista di dict con almeno 'cf', 'data_fine', 'ragione_sociale',
                 'tipo_delega'.
        soglia_giorni: giorni entro cui considerare una delega in scadenza
                       (default 20).

    Returns:
        Dict con:
          - ``scadute``: lista deleghe già scadute
          - ``in_scadenza``: lista deleghe che scadranno entro ``soglia_giorni``
          - ``valide``: lista deleghe ancora valide (con data_fine futura)
          - ``senza_data``: lista deleghe senza data_fine
          - ``data_controllo``: data del controllo (YYYY-MM-DD)
    """
    from datetime import datetime, date

    oggi = date.today()
    data_controllo = oggi.strftime("%Y-%m-%d")

    scadute: List[Dict] = []
    in_scadenza: List[Dict] = []
    valide: List[Dict] = []
    senza_data: List[Dict] = []

    for d in deleghe:
        data_fine_str = d.get("data_fine", "").strip()
        if not data_fine_str:
            senza_data.append(d)
            continue

        # Prova vari formati di data
        data_fine = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                data_fine = datetime.strptime(data_fine_str, fmt).date()
                break
            except ValueError:
                continue

        if data_fine is None:
            senza_data.append(d)
            continue

        giorni_mancanti = (data_fine - oggi).days

        entry = {
            **d,
            "data_fine_parsed": data_fine_str,
            "giorni_mancanti": giorni_mancanti,
        }

        if giorni_mancanti < 0:
            scadute.append(entry)
        elif giorni_mancanti <= soglia_giorni:
            in_scadenza.append(entry)
        else:
            valide.append(entry)

    return {
        "data_controllo": data_controllo,
        "soglia_giorni": soglia_giorni,
        "scadute": scadute,
        "in_scadenza": in_scadenza,
        "valide": valide,
        "senza_data": senza_data,
        "riepilogo": {
            "totale": len(deleghe),
            "scadute": len(scadute),
            "in_scadenza": len(in_scadenza),
            "valide": len(valide),
            "senza_data": len(senza_data),
        },
    }


__all__ = [
    "fetch_active_deleghe",
    "fetch_clients_from_wizard",
    "fetch_deleghe_dirette_from_wizard",
    "fetch_all_deleghe",
    "fetch_all_deleghe_enhanced",
    "load_deleghe_from_csv",
    "salva_ragione_sociale",
    "check_delegations_validity",
    "generate_expiry_report",
    "_debug_probe_endpoints",
    "_extract_cf_list_from_response",
    "DEFAULT_CSV_DELEGHE",
    "RAGIONI_SOCIALI_FILE",
    "CANDIDATE_ENDPOINTS",
    "TIPO_DELEGA_INCARICATO",
    "TIPO_DELEGA_DIRETTA",
]
