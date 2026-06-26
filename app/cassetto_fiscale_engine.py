"""
Cassetto Fiscale dell'Agenzia delle Entrate
=============================================

Motore per l'accesso e lo scarico di documenti dal Cassetto Fiscale
(cassetto.agenziaentrate.gov.it), un sistema SEPARATO da "Fatture e
Corrispettivi" (ivaservizi).

Utilizza la stessa sessione SSO del portale (login IAMPE), ma richiede
una inizializzazione esplicita della sessione sul dominio Cassetto
prima di poter interrogare gli endpoint.

Endpoint scoperti tramite reverse engineering di RecuFatture.exe:
  - initCassetto  : POST /casshome-rest/rs/initCassetto?v=
  - initLight     : GET  /casshome-rest/rs/initLight?v=
  - cambiaCliente : POST /casshome-rest/rs/cambiaCliente?v=
  - CassettoFiscaleServlet : GET /cassfisc-web/CassettoFiscaleServlet?Ric=TIPO&Anno=ANNO

Il parametro "Ric" determina il tipo di documento:
  F24     = F24 generici (tasse, contributi)
  DetF24  = Dettaglio F24
  RED     = Modello Redditi
  730     = Modello 730
  770     = Modello 770 (sostituti d'imposta)
  UNI     = Unico
  IVA     = Dichiarazione IVA
  IRA     = IRAP
  CUK     = CU familiari
  ITR     = ITR
  REDD    = Redditi (dettaglio)
  VERS    = Versamenti
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlencode, parse_qs

import requests
from lxml import etree, html

from app.engine import unix_ms
from app.security import get_ca_bundle

# ─── Browser fallback (opzionale, solo se playwright installato) ─────────────

_BROWSER_ENGINE = None  # type: ignore

def _get_browser_engine():
    """Importa e restituisce la classe CassettoFiscaleBrowserEngine."""
    global _BROWSER_ENGINE
    if _BROWSER_ENGINE is None:
        try:
            from app.cassetto_fiscale_browser import (
                CassettoFiscaleBrowserEngine as BE,
                PLAYWRIGHT_AVAILABLE,
            )
            if PLAYWRIGHT_AVAILABLE:
                _BROWSER_ENGINE = BE
        except ImportError:
            _BROWSER_ENGINE = False  # type: ignore
        except Exception:
            _BROWSER_ENGINE = False  # type: ignore
    return _BROWSER_ENGINE if _BROWSER_ENGINE else None

# ─── Costanti ───────────────────────────────────────────────────────────────────

CASSETTO_BASE = "https://cassetto.agenziaentrate.gov.it"
CASSETTO_HOME = f"{CASSETTO_BASE}/CassHomeWeb/home"
CASSETTO_REST = f"{CASSETTO_BASE}/casshome-rest/rs"
CASSETTO_SERVLET = f"{CASSETTO_BASE}/cassfisc-web/CassettoFiscaleServlet"

# Tipi di documento disponibili sul Cassetto Fiscale
TIPI_DOCUMENTO: Dict[str, str] = {
    "F24": "F24 generici (tasse, contributi)",
    "DetF24": "Dettaglio F24",
    "RED": "Modello Redditi",
    "730": "Modello 730",
    "770": "Modello 770 (sostituti d'imposta)",
    "UNI": "Unico",
    "IVA": "Dichiarazione IVA",
    "IRA": "IRAP",
    "CUK": "CU familiari / Certificazione Unica",
    "ITR": "ITR",
    "REDD": "Redditi (dettaglio)",
    "VERS": "Versamenti",
}

# Mappa inversa: nome breve -> codice Ric
TIPO_TO_RIC: Dict[str, str] = {v.lower(): k for k, v in TIPI_DOCUMENTO.items()}
TIPO_TO_RIC.update({
    "redditi": "RED",
    "redditi_pf": "RED",
    "redditi_sp": "RED",
    "redditi_sc": "RED",
    "730": "730",
    "770": "770",
    "unico": "UNI",
    "iva": "IVA",
    "irap": "IRA",
    "f24": "F24",
    "f24 generici": "F24",
    "dettaglio f24": "DetF24",
    "cu": "CUK",
    "certificazione unica": "CUK",
})


# ─── Eccezioni ──────────────────────────────────────────────────────────────────


class CassettoFiscaleError(Exception):
    """Errore generico del Cassetto Fiscale."""
    pass


class CassettoNotInitializedError(CassettoFiscaleError):
    """Il Cassetto Fiscale non è stato inizializzato."""
    pass


# ─── Engine principale ──────────────────────────────────────────────────────────


class CassettoFiscaleEngine:
    """
    Motore per l'accesso al Cassetto Fiscale dell'Agenzia delle Entrate.

    Utilizza la sessione requests già autenticata (da FEScraperEngine.login)
    e la inizializza sul dominio cassetto.agenziaentrate.gov.it via SSO.

    Per intermediari (incaricati), supporta il cambio cliente tramite
    l'endpoint cambiaCliente.

    Esempio:
        from app.engine import FEScraperEngine
        engine = FEScraperEngine(logger)
        engine.login(cf, pin, password)

        cf = CassettoFiscaleEngine(engine.session, logger)
        cf.init_session()

        # F24 generici per un cliente
        f24_list = cf.fetch_document_list("F24", 2024, piva="01234567890")
        for doc in f24_list:
            cf.download_document(doc["url"], "output/")

        # Dichiarazioni dei redditi
        red_list = cf.fetch_document_list("RED", 2024, piva="01234567890")
    """

    def __init__(
        self,
        session: requests.Session,
        logger: Callable[..., None],
    ):
        """
        Args:
            session: Sessione requests già autenticata (dopo login IAMPE).
            logger: Funzione di logging (es. logger.info o print).
        """
        self.session = session
        self.logger = logger
        self._initialized = False
        self._chiave_cassetto: Optional[str] = None
        self._current_piva: Optional[str] = None

    # ── Proprietà ─────────────────────────────────────────────────────────────

    @property
    def initialized(self) -> bool:
        """True se init_session() è stata chiamata con successo."""
        return self._initialized

    @property
    def chiave_cassetto(self) -> Optional[str]:
        """Chiave di sessione del Cassetto Fiscale (se disponibile)."""
        return self._chiave_cassetto

    # ── Inizializzazione sessione ─────────────────────────────────────────────

    def init_session(self) -> Dict[str, Any]:
        """
        Inizializza la sessione sul Cassetto Fiscale.

        Flusso:
          1. GET CassHomeWeb/home  → SSO redirect + set cookie
          2. GET /rs/initLight     → init leggera per JS
          3. POST /rs/initCassetto → inizializzazione vera e propria

        Returns:
            Dict con la risposta JSON di initCassetto.

        Raises:
            CassettoFiscaleError: se l'inizializzazione fallisce.
        """
        self.logger("Inizializzazione sessione Cassetto Fiscale...")

        # Step 1: home page → attiva SSO redirect e imposta cookie
        try:
            r = self.session.get(
                CASSETTO_HOME,
                allow_redirects=True,
                verify=get_ca_bundle(),
                timeout=30,
            )
        except requests.RequestException as e:
            raise CassettoFiscaleError(
                f"Connessione a Cassetto Fiscale fallita: {e}"
            )

        if r.status_code not in (200, 302):
            raise CassettoFiscaleError(
                f"Home Cassetto Fiscale non raggiungibile "
                f"(HTTP {r.status_code}). "
                f"Forse la sessione SSO non è più valida."
            )

        # Step 2: initLight (prepara il JS client)
        try:
            r2 = self.session.get(
                f"{CASSETTO_REST}/initLight?v={unix_ms()}",
                verify=get_ca_bundle(),
                timeout=15,
            )
        except requests.RequestException as e:
            self.logger(f"  InitLight: errore (non bloccante): {e}")
        else:
            self.logger(f"  InitLight: HTTP {r2.status_code}")

        # Step 3: initCassetto (POST)
        # NOTA: HTTP 409 ≠ "già inizializzato". Può anche significare
        # "sessione scaduta/non valida". In tal caso, riproviamo con
        # una GET a CassHomeWeb/home per forzare un refresh SSO.
        try:
            r3 = self.session.post(
                f"{CASSETTO_REST}/initCassetto?v={unix_ms()}",
                json={},
                headers={"Content-Type": "application/json"},
                verify=get_ca_bundle(),
                timeout=15,
            )
        except requests.RequestException as e:
            raise CassettoFiscaleError(
                f"Init Cassetto Fiscale fallito: {e}"
            )

        if r3.status_code == 409:
            # Potrebbe essere sessione scaduta → riprova con refresh SSO
            self.logger(
                "  InitCassetto: HTTP 409 — provo refresh SSO..."
            )
            # Re-visita la home per refresh SSO cookie
            try:
                r_home2 = self.session.get(
                    CASSETTO_HOME,
                    allow_redirects=True,
                    verify=get_ca_bundle(),
                    timeout=30,
                )
                self.logger(f"  Refresh home: HTTP {r_home2.status_code}")
            except requests.RequestException as e:
                self.logger(f"  Refresh home fallito: {e}")

            # Riprova initCassetto
            try:
                r3 = self.session.post(
                    f"{CASSETTO_REST}/initCassetto?v={unix_ms()}",
                    json={},
                    headers={"Content-Type": "application/json"},
                    verify=get_ca_bundle(),
                    timeout=15,
                )
                self.logger(
                    f"  InitCassetto (ritentativo): HTTP {r3.status_code}"
                )
            except requests.RequestException as e:
                raise CassettoFiscaleError(
                    f"Init Cassetto Fiscale (ritentativo) fallito: {e}"
                )

        # Dopo il ritentativo, accetta 200 o 409
        if r3.status_code == 409:
            self.logger(
                "  InitCassetto: HTTP 409 persistente. "
                "Proseguo assumendo sessione già attiva."
            )
            self._initialized = True
            return {"esito": "OK", "note": "sessione 409 persistente"}

        if r3.status_code != 200:
            raise CassettoFiscaleError(
                f"Init Cassetto Fiscale: HTTP {r3.status_code}. "
                f"Forse l'utenza non ha accesso al Cassetto Fiscale."
            )

        try:
            data = r3.json()
        except Exception as e:
            raise CassettoFiscaleError(
                f"Init Cassetto Fiscale: JSON malformato: {e}"
            )

        # Estrai eventuale chiave cassetto
        self._chiave_cassetto = (
            data.get("chiaveCassetto")
            or data.get("chiave")
            or data.get("sessionKey")
            or data.get("idSessione")
        )

        self._initialized = True
        self.logger(
            f"Cassetto Fiscale inizializzato. "
            f"Chiave: {self._chiave_cassetto or 'N/D'}"
        )
        return data

    # ── Cambio cliente (per intermediari) ──────────────────────────────────────

    def cambia_cliente(self, piva: str) -> Dict[str, Any]:
        """
        Cambia il cliente attivo sul Cassetto Fiscale (per intermediari).

        Prova prima con ``{"pIva": piva}``; se la risposta è 409, riprova
        con ``{"cf": piva}`` (utile per CF di persona fisica che non ha
        una partita IVA separata).

        Args:
            piva: Partita IVA (o CF) del cliente.

        Returns:
            Dict con la risposta JSON.

        Raises:
            CassettoNotInitializedError: se init_session() non è stata chiamata.
            CassettoFiscaleError: se il cambio cliente fallisce.
        """
        if not self._initialized:
            raise CassettoNotInitializedError(
                "Chiamare init_session() prima di cambia_cliente()."
            )

        self.logger(f"Cassetto Fiscale: cambio cliente a {piva}...")

        # Tentativo 1: con pIva
        body = {"pIva": piva}
        r = self._try_cambia_cliente(body)
        if r is not None:
            self._current_piva = piva
            self.logger(f"Cassetto Fiscale: cliente cambiato a {piva} (body={body}).")
            return r

        # Tentativo 2: con cf
        body = {"cf": piva}
        r = self._try_cambia_cliente(body)
        if r is not None:
            self._current_piva = piva
            self.logger(f"Cassetto Fiscale: cliente cambiato a {piva} (body={body}).")
            return r

        # Entrambi falliti
        raise CassettoFiscaleError(
            f"Cambio cliente: HTTP 409 anche con 'cf'. "
            f"Verifica che {piva} abbia una delega attiva "
            f"sul Cassetto Fiscale."
        )

    def _try_cambia_cliente(self, body: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Prova una chiamata a cambiaCliente con un body specifico."""
        try:
            r = self.session.post(
                f"{CASSETTO_REST}/cambiaCliente?v={unix_ms()}",
                json=body,
                headers={"Content-Type": "application/json"},
                verify=get_ca_bundle(),
                timeout=15,
            )
        except requests.RequestException as e:
            self.logger(f"  Cambio cliente fallito (body={body}): {e}")
            return None

        if r.status_code in (200, 204):
            return r.json() if r.status_code == 200 and r.text else {"esito": "OK"}

        if r.status_code == 409:
            try:
                body_text = r.json()
                self.logger(
                    f"  Cambio cliente 409 (body={body}): {json.dumps(body_text)}"
                )
            except Exception:
                self.logger(
                    f"  Cambio cliente 409 (body={body}): {r.text[:200] if r.text else '(vuoto)'}"
                )
            return None

        # Altri errori
        self.logger(
            f"  Cambio cliente: HTTP {r.status_code} (body={body})"
        )
        return None

    # ── Navigazione servlet ────────────────────────────────────────────────────

    def _build_servlet_url(
        self,
        ric: str,
        anno: Optional[int] = None,
        extra_params: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Costruisce l'URL per la CassettoFiscaleServlet.

        Args:
            ric: Codice del tipo di documento (es. "F24", "RED", "730").
            anno: Anno di riferimento (opzionale per alcuni tipi).
            extra_params: Parametri aggiuntivi (es. chiave, cf, ecc.)

        Returns:
            URL completo della servlet.
        """
        params: Dict[str, str] = {"Ric": ric}
        if anno is not None:
            params["Anno"] = str(anno)
        if self._chiave_cassetto:
            params["chiave"] = self._chiave_cassetto
        if extra_params:
            params.update(extra_params)

        return f"{CASSETTO_SERVLET}?{urlencode(params)}"

    def navigate_servlet(
        self,
        ric: str,
        anno: Optional[int] = None,
        piva: Optional[str] = None,
        extra_params: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str]:
        """
        Naviga alla CassettoFiscaleServlet e restituisce l'HTML.

        Per intermediari, prova prima cambia_cliente(). Se fallisce (es.
        HTTP 409 perché il cliente non è abilitato al Cassetto Fiscale),
        passa piva/cf come parametri extra direttamente alla servlet.

        Args:
            ric: Codice del tipo di documento.
            anno: Anno di riferimento.
            piva: Per intermediari, P.IVA del cliente.
            extra_params: Parametri aggiuntivi per la servlet.

        Returns:
            Tupla (status_code, html_text).

        Raises:
            CassettoNotInitializedError: se init_session() non è stata chiamata.
        """
        if not self._initialized:
            raise CassettoNotInitializedError(
                "Chiamare init_session() prima di navigate_servlet()."
            )

        # Cambia cliente se necessario (non bloccante)
        if piva and piva != self._current_piva:
            try:
                self.cambia_cliente(piva)
            except CassettoFiscaleError as e:
                self.logger(
                    f"  Cambio cliente non riuscito: {e}. "
                    f"Provo a passare piva come parametro servlet..."
                )
                # Passa piva come parametro extra alla servlet
                extra_params = dict(extra_params or {})
                extra_params["cf"] = piva
                extra_params["pIva"] = piva

        url = self._build_servlet_url(ric, anno, extra_params)
        self.logger(f"Navigazione Cassetto: Ric={ric} Anno={anno} P.IVA={piva or '-'}")

        try:
            r = self.session.get(
                url,
                verify=get_ca_bundle(),
                timeout=30,
            )
        except requests.RequestException as e:
            raise CassettoFiscaleError(
                f"Navigazione servlet fallita: {e}"
            )

        return r.status_code, r.text

    # ── Estrazione dati da HTML ────────────────────────────────────────────────

    def parse_table_from_html(self, html_text: str) -> List[Dict[str, str]]:
        """
        Estrae i dati da una tabella HTML restituita dalla servlet.

        Cerca tabelle con classi contenenti "elenco", "tabella", "dati",
        o semplicemente la prima tabella <table> con più di 2 righe.

        Args:
            html_text: HTML restituito dalla servlet.

        Returns:
            Lista di dict, dove ogni dict rappresenta una riga della tabella.
        """
        try:
            tree = html.fromstring(html_text)
        except Exception as e:
            self.logger(f"  Parsing HTML fallito: {e}")
            return []

        # Cerca tabelle con classi significative
        tables = tree.xpath(
            "//table[contains(@class, 'elenco') or "
            "contains(@class, 'tabella') or "
            "contains(@class, 'dati') or "
            "contains(@class, 'risultati') or "
            "contains(@class, 'grid')]"
        )

        if not tables:
            # Fallback: la prima tabella con almeno 3 righe
            tables = tree.xpath("//table[count(tr) >= 3]")

        if not tables:
            # Fallback estremo: qualunque tabella
            tables = tree.xpath("//table")

        if not tables:
            self.logger("  Nessuna tabella trovata nell'HTML.")
            return []

        results: List[Dict[str, str]] = []
        for table in tables[:1]:  # Prima tabella rilevante
            rows = table.xpath(".//tr")
            if not rows:
                continue

            # Intestazioni: prendi dalla prima riga (<th> o primo <td>)
            headers: List[str] = []
            header_cells = rows[0].xpath("th | td")
            for cell in header_cells:
                h = cell.text_content().strip()
                headers.append(h)

            # Righe dati
            for row in rows[1:]:
                cells = row.xpath("td")
                if not cells:
                    continue
                record: Dict[str, str] = {}
                for idx, cell in enumerate(cells):
                    key = headers[idx] if idx < len(headers) else f"col{idx}"
                    value = cell.text_content().strip()
                    record[key] = value

                    # Cerca link nella cella
                    links = cell.xpath(".//a")
                    if links:
                        href = links[0].get("href", "")
                        if href:
                            # Se è un link relativo, costruisci URL assoluto
                            if href.startswith("/"):
                                href = urljoin(CASSETTO_BASE, href)
                            elif not href.startswith("http"):
                                href = urljoin(
                                    CASSETTO_SERVLET, href
                                )
                            record[f"{key}_link"] = href
                            record["url"] = href
                            record["href"] = href

                if record:
                    results.append(record)

        return results

    def parse_document_links_from_html(
        self, html_text: str
    ) -> List[Dict[str, str]]:
        """
        Estrae i link ai documenti (PDF, P7M) dall'HTML della servlet.

        Cerca link che puntano a file PDF, P7M o a servlet di download.

        Args:
            html_text: HTML restituito dalla servlet.

        Returns:
            Lista di dict con 'url', 'testo', 'tipo'.
        """
        try:
            tree = html.fromstring(html_text)
        except Exception as e:
            self.logger(f"  Parsing HTML fallito: {e}")
            return []

        documents: List[Dict[str, str]] = []

        # Cerca tutti i link a file PDF e P7M
        for a in tree.xpath("//a[@href]"):
            href = a.get("href", "").strip()
            testo = a.text_content().strip()

            if not href:
                continue

            # Costruisci URL assoluto
            if href.startswith("/"):
                full_url = urljoin(CASSETTO_BASE, href)
            elif href.startswith("http"):
                full_url = href
            else:
                full_url = urljoin(CASSETTO_SERVLET, href)

            # Determina tipo
            href_lower = href.lower()
            if ".pdf" in href_lower:
                tipo = "PDF"
            elif ".p7m" in href_lower:
                tipo = "P7M"
            elif "download" in href_lower or "scarica" in href_lower:
                tipo = "DOWNLOAD"
            elif full_url != href and full_url != CASSETTO_SERVLET:
                # Altro link relativo (potrebbe essere una pagina di dettaglio)
                tipo = "PAGINA"
            else:
                continue

            documents.append({
                "url": full_url,
                "testo": testo or "documento",
                "tipo": tipo,
            })

        return documents

    # ── Operazioni principali ───────────────────────────────────────────────────

    def fetch_document_list(
        self,
        tipo: str,
        anno: int,
        piva: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recupera l'elenco dei documenti per tipo e anno.

        Args:
            tipo: Tipo documento (es. "F24", "RED", "730", "770").
                  Oppure nome descrittivo (es. "f24 generici", "redditi").
            anno: Anno di riferimento.
            piva: P.IVA del cliente (obbligatoria per intermediari).

        Returns:
            Lista di dict con i dati estratti dalla tabella HTML.
        """
        # Normalizza il tipo
        ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())

        status, html_text = self.navigate_servlet(ric, anno, piva=piva)

        if status not in (200, 204):
            self.logger(
                f"  Documenti {ric}/{anno}: HTTP {status} — "
                f"nessun dato disponibile."
            )
            return []

        if status == 204 or not html_text.strip():
            self.logger(f"  Documenti {ric}/{anno}: risposta vuota.")
            return []

        # Prova prima come tabella
        records = self.parse_table_from_html(html_text)

        # Se nessuna tabella, cerca link a documenti
        if not records:
            docs = self.parse_document_links_from_html(html_text)
            if docs:
                records = [
                    {
                        "tipo": d["tipo"],
                        "url": d["url"],
                        "descrizione": d["testo"],
                    }
                    for d in docs
                ]

        self.logger(
            f"  Documenti {ric}/{anno}: trovati {len(records)} elementi."
        )

        # Debug: salva HTML per analisi (sempre, per capire la struttura)
        debug_path = f"/tmp/cassetto_debug_{ric}_{anno}.html"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html_text)
        self.logger(f"  HTML salvato per debug: {debug_path}")

        # Filtra link spuri (es. Ric=HOME, Ric=Help, link di navigazione)
        if records:
            records = [
                r for r in records
                if r.get("url") and "Ric=HOME" not in r["url"]
                and "Ric=Help" not in r["url"]
                and "Ric=Menu" not in r["url"]
            ]
            if not records:
                self.logger(
                    f"  Tutti i link filtrati come navigazione. "
                    f"Nessun documento reale trovato."
                )

        return records

    def download_document(
        self,
        url: str,
        output_dir: str,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Scarica un documento (PDF/P7M) dal Cassetto Fiscale.

        Args:
            url: URL del documento (da fetch_document_list).
            output_dir: Directory dove salvare il file.
            filename: Nome file (opzionale; altrimenti estratto dall'URL).

        Returns:
            Path del file salvato, oppure None in caso di errore.
        """
        self.logger(f"Download documento: {url}")

        # Alcuni endpoint richiedono il Referer per scaricare
        headers = {
            "Referer": f"{CASSETTO_SERVLET}?Ric=&Anno=",
            "Accept": "application/pdf, application/p7m, application/octet-stream, */*",
        }

        try:
            r = self.session.get(
                url,
                headers=headers,
                verify=get_ca_bundle(),
                timeout=60,
                stream=True,
            )
        except requests.RequestException as e:
            self.logger(f"  Download fallito: {e}")
            return None

        if r.status_code != 200:
            self.logger(f"  Download: HTTP {r.status_code}")
            return None

        # Determina estensione dal Content-Type
        ct = r.headers.get("Content-Type", "")
        log_ct = ct
        if "pdf" in ct:
            ext = ".pdf"
        elif "p7m" in ct or "pkcs7" in ct:
            ext = ".p7m"
        elif "xml" in ct:
            ext = ".xml"
        elif "html" in ct:
            ext = ".html"
        else:
            ext = ".bin"

        # Log diagnostico: Content-Type e primi byte
        body_preview = r.content[:200] if r.content else b"(vuoto)"
        is_pdf_magic = r.content[:5] == b"%PDF-"
        self.logger(
            f"  Download response: Content-Type={log_ct}, "
            f"size={len(r.content)} byte, "
            f"PDF magic={is_pdf_magic}, "
            f"primi byte={body_preview[:80]}"
        )
        # Se sembra HTML ma ci aspettavamo PDF, logghiamolo
        if ext == ".html" and filename and filename.lower().endswith(".pdf"):
            self.logger(
                f"  ATTENZIONE: il Content-Type è HTML ma il filename richiede PDF. "
                f"Salvo comunque come .pdf per compatibilità."
            )
            ext = ".pdf"  # Forza estensione PDF nonostante l'HTML

        # Nome file
        if not filename:
            # Estrai dall'URL o dal Content-Disposition
            disposition = r.headers.get("Content-Disposition", "")
            match = re.search(r'filename=["\']?([^"\']+)', disposition)
            if match:
                filename = match.group(1)
            else:
                # Usa l'ultimo segmento dell'URL
                filename = url.rstrip("/").split("/")[-1].split("?")[0]
                if not filename:
                    filename = f"documento_{unix_ms()}"

            # Aggiungi estensione se mancante
            if not filename.lower().endswith(ext):
                filename += ext

        # Salva
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        size = os.path.getsize(filepath)
        self.logger(f"  Salvato: {filepath} ({size} byte)")
        return filepath

    # ── Funzioni aggregate ─────────────────────────────────────────────────────

    def fetch_f24_list(
        self,
        anno: int,
        piva: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recupera l'elenco degli F24 generici per anno."""
        return self.fetch_document_list("F24", anno, piva=piva)

    def fetch_dichiarazioni_list(
        self,
        anno: int,
        tipo: str = "RED",
        piva: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recupera l'elenco delle dichiarazioni per anno.

        Args:
            anno: Anno di riferimento.
            tipo: Tipo dichiarazione ("RED", "730", "770", "UNI", "IVA", "IRA").
            piva: P.IVA del cliente.

        Returns:
            Lista di documenti.
        """
        return self.fetch_document_list(tipo, anno, piva=piva)


# ─── Funzioni di utilità ────────────────────────────────────────────────────────


def get_tipi_documento() -> Dict[str, str]:
    """Restituisce la mappa dei tipi di documento disponibili."""
    return dict(TIPI_DOCUMENTO)


def export_json(
    records: List[Dict[str, Any]],
    cf: str,
    anno: int,
    categoria: str,
    output_dir: str,
) -> str:
    """Salva i metadati dei documenti in JSON."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{categoria}_{cf}_{anno}.json"
    path = os.path.join(output_dir, filename)

    payload = {
        "cf": cf,
        "anno": anno,
        "categoria": categoria,
        "data_estrazione": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totale": len(records),
        "documenti": records,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path


# ─── Run wrapper (per menu.py) ──────────────────────────────────────────────────


def run_f24_generici(
    engine: CassettoFiscaleEngine,
    cf: str,
    piva: str,
    anno: int,
    logger: Callable[..., None],
    output_root: str = "output",
    download_pdf: bool = True,
    client_dir: Optional[str] = None,
) -> bool:
    """
    Esegue il download completo degli F24 generici per un cliente e anno.

    Args:
        engine: CassettoFiscaleEngine già inizializzato.
        cf: Codice fiscale del cliente.
        piva: Partita IVA del cliente.
        anno: Anno di riferimento.
        logger: Funzione di logging.
        output_root: Directory radice per l'output.
        download_pdf: Se True, scarica anche i PDF.
        client_dir: Nome directory personalizzato per il cliente (es. "RIZZO_ANGELA").
                    Se None, usa ``piva``.

    Returns:
        True se completato con successo.
    """
    logger(f"\n=== Download F24 generici per {cf}/{piva} anno {anno} ===")

    records = engine.fetch_f24_list(anno, piva=piva)

    if not records:
        logger(f"Nessun F24 trovato per {cf} anno {anno}")
        return True

    # Directory output: output/<CLIENTE>/<ANNO>/f24generici/
    dir_name = client_dir or piva
    base_output = os.path.join(output_root, dir_name, str(anno), "f24generici")

    # Salva metadati in JSON
    json_path = export_json(records, cf, anno, "F24_GENERICI", base_output)
    logger(f"F24 generici: JSON metadati salvato in {json_path}")

    # Salva PDF se richiesto
    if download_pdf:
        pdf_dir = os.path.join(base_output, "PDF")
        scaricati = 0
        for doc in records:
            url = doc.get("url") or doc.get("href") or ""
            if not url:
                continue
            # Costruisci filename dal contenuto
            desc = doc.get("descrizione") or doc.get("oggetto") or ""
            data = doc.get("data") or doc.get("Data") or str(anno)
            importi = doc.get("importo") or doc.get("Importo") or ""
            fname = f"F24_{data}_{importi}.pdf".replace("/", "_").replace(" ", "_")
            fpath = engine.download_document(url, pdf_dir, filename=fname)
            if fpath:
                scaricati += 1

        logger(f"F24 generici: scaricati {scaricati}/{len(records)} documenti")

    return True


def run_dichiarazioni(
    engine: CassettoFiscaleEngine,
    cf: str,
    piva: str,
    anno: int,
    logger: Callable[..., None],
    output_root: str = "output",
    tipo: str = "RED",
    download_pdf: bool = True,
    client_dir: Optional[str] = None,
) -> bool:
    """
    Esegue il download completo delle dichiarazioni per un cliente e anno.

    Args:
        engine: CassettoFiscaleEngine già inizializzato.
        cf: Codice fiscale del cliente.
        piva: Partita IVA del cliente.
        anno: Anno di riferimento.
        logger: Funzione di logging.
        output_root: Directory radice per l'output.
        tipo: Tipo dichiarazione ("RED", "730", "770", "UNI", "IVA", "IRA").
        download_pdf: Se True, scarica anche i PDF.
        client_dir: Nome directory personalizzato per il cliente (es. "RIZZO_ANGELA").
                    Se None, usa ``piva``.

    Returns:
        True se completato con successo.
    """
    ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())
    tipo_label = TIPI_DOCUMENTO.get(ric, ric)
    logger(f"\n=== Download {tipo_label} per {cf}/{piva} anno {anno} ===")

    records = engine.fetch_dichiarazioni_list(anno, tipo=ric, piva=piva)

    if not records:
        logger(f"Nessuna dichiarazione trovata per {cf} anno {anno}")
        return True

    # Directory output: output/<CLIENTE>/<ANNO>/<categoria>/
    dir_name = client_dir or piva
    if ric == "CUK":
        categ_folder = "certificazioniuniche"
    else:
        categ_folder = f"dichiarazioni_{ric.lower()}"
    base_output = os.path.join(output_root, dir_name, str(anno), categ_folder)

    # Salva metadati in JSON
    json_path = export_json(records, cf, anno, ric, base_output)
    logger(f"Dichiarazioni: JSON metadati salvato in {json_path}")

    # Salva PDF se richiesto
    if download_pdf:
        pdf_dir = os.path.join(base_output, "PDF")
        scaricati = 0
        for doc in records:
            url = doc.get("url") or doc.get("href") or ""
            if not url:
                continue
            desc = doc.get("descrizione") or doc.get("oggetto") or ""
            data = doc.get("data") or doc.get("Data") or str(anno)
            fname = f"Dich_{ric}_{data}_{desc}.pdf".replace("/", "_").replace(" ", "_")
            fpath = engine.download_document(url, pdf_dir, filename=fname)
            if fpath:
                scaricati += 1

        logger(f"Dichiarazioni: scaricati {scaricati}/{len(records)} documenti")

    return True

    # Directory output: output/<PIVA>/<ANNO>/certificazioniuniche/ (o dichiarazioni_*)
    if ric == "CUK":
        categ_folder = "certificazioniuniche"
    else:
        categ_folder = f"dichiarazioni_{ric.lower()}"
    base_output = os.path.join(output_root, piva, str(anno), categ_folder)

    # Salva metadati in JSON
    json_path = export_json(records, cf, anno, categ, base_output)
    logger(f"Dichiarazioni: JSON metadati salvato in {json_path}")

    # Salva PDF se richiesto
    if download_pdf:
        pdf_dir = os.path.join(base_output, "PDF")
        scaricati = 0
        for doc in records:
            url = doc.get("url") or doc.get("href") or ""
            if not url:
                continue
            desc = doc.get("descrizione") or doc.get("oggetto") or ""
            data = doc.get("data") or doc.get("Data") or str(anno)
            fname = f"Dich_{ric}_{data}_{desc}.pdf".replace("/", "_").replace(" ", "_")
            fpath = engine.download_document(url, pdf_dir, filename=fname)
            if fpath:
                scaricati += 1

        logger(f"Dichiarazioni: scaricati {scaricati}/{len(records)} documenti")

    return True


__all__ = [
    "CassettoFiscaleEngine",
    "CassettoFiscaleError",
    "CassettoNotInitializedError",
    "get_tipi_documento",
    "TIPI_DOCUMENTO",
    "TIPO_TO_RIC",
    "export_json",
    "run_f24_generici",
    "run_dichiarazioni",
    "CASSETTO_BASE",
    "CASSETTO_HOME",
    "CASSETTO_REST",
    "CASSETTO_SERVLET",
]
