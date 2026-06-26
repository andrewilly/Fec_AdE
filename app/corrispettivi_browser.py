"""
Corrispettivi — Dettaglio via Playwright (browser automation).

Usa un browser headless Chromium per accedere al dettaglio dei corrispettivi
sul portale "Fatture e Corrispettivi" (cons-web) e leggere la tabella
"Dati contabili del corrispettivo" che contiene la ripartizione per aliquota IVA.

Il JSON API (sintesi/elenco) non restituisce la ripartizione per aliquota,
ma il portale web la mostra in una tabella HTML accessibile via browser.

Cross-platform: macOS e Windows.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from lxml import html as lxml_html

# ─── Tentativo import Playwright ──────────────────────────────────────────────

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

# ─── Costanti ──────────────────────────────────────────────────────────────────

IAMPE_LOGIN_URL = (
    "https://iampe.agenziaentrate.gov.it/sam/UI/Login"
    "?realm=/agenziaentrate"
    "&goto=https%3A%2F%2Fportale.agenziaentrate.gov.it%3A443%2FPortaleWeb%2Fhome"
)

LOGIN_SELECTORS = {
    "tab_fisconline": "text=Fisconline/Entratel",
    "cf_input": "#username-fo-ent",
    "password_input": "#password-fo-ent-1",
    "pin_input": "#pin-fo-ent",
    "submit_btn": "button:has-text('Accedi')",
}

CONS_WEB = "https://ivaservizi.agenziaentrate.gov.it/cons/cons-web"
CONS_WEB_SPA = f"{CONS_WEB}/#/corrispettivi/puntuale"


class CorrispettiviBrowserError(Exception):
    """Errore generico del browser corrispettivi."""
    pass


class CorrispettiviBrowserEngine:
    """
    Motore per l'accesso ai dettagli dei corrispettivi via Playwright.

    Usa un browser headless per:
      1. Login SSO (IAMPE)
      2. Navigazione a "Fatture e Corrispettivi"
      3. Accesso alla pagina di dettaglio di un corrispettivo
      4. Estrazione della tabella "Dati contabili del corrispettivo"
    """

    def __init__(
        self,
        logger: Callable[..., None],
        headless: bool = True,
        browser_type: str = "chromium",
    ):
        self.logger = logger
        self.headless = headless
        self.browser_type = browser_type
        self._initialized = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    def close(self):
        """Rilascia le risorse del browser."""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def init_session(self, cf: str, pin: str, password: str) -> Dict[str, Any]:
        """
        Avvia il browser, login SSO e naviga a Fatture e Corrispettivi.
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise CorrispettiviBrowserError(
                "Playwright non installato. "
                "Esegui: pip install playwright && "
                "python -m playwright install chromium"
            )

        self.logger("Avvio browser per login SSO Fatture e Corrispettivi...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = getattr(self._playwright, self.browser_type).launch(
                headless=self.headless,
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()

            self._sso_login(cf, pin, password)
            self._navigate_to_cons_web()

            self._initialized = True
            self.logger("Corrispettivi browser inizializzato.")
            return {"esito": "OK", "metodo": "playwright"}

        except Exception as e:
            self.close()
            raise CorrispettiviBrowserError(
                f"Init corrispettivi browser fallito: {e}"
            )

    def init_session_from_requests(self, session: "requests.Session") -> Dict[str, Any]:
        """
        Avvia il browser importando i cookie da una sessione requests
        già autenticata (con x-appl e token B2B).

        Questo evita il login SSO via browser e il problema del redirect
        SAML per ivaservizi. I cookie vengono impostati direttamente
        nel contesto Playwright prima della navigazione.
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise CorrispettiviBrowserError("Playwright non installato.")

        self.logger("Avvio browser con cookie da sessione requests...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = getattr(self._playwright, self.browser_type).launch(
                headless=self.headless,
            )

            # Estrai cookie dalla sessione requests
            cookies = []
            for c in session.cookies:
                cookie_dict = {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or "portale.agenziaentrate.gov.it",
                    "path": c.path or "/",
                }
                if c.secure:
                    cookie_dict["secure"] = True
                if hasattr(c, "expires") and c.expires:
                    cookie_dict["expires"] = c.expires
                cookies.append(cookie_dict)

            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                # Imposta i cookie PRIMA di creare la pagina
                storage_state={"cookies": cookies} if cookies else None,
            )

            # Aggiungi anche i cookie per ivaservizi (stessi cookie di sessione)
            if cookies:
                ivaservizi_cookies = []
                for c in cookies:
                    cc = dict(c)
                    cc["domain"] = "ivaservizi.agenziaentrate.gov.it"
                    ivaservizi_cookies.append(cc)
                # context.add_cookies() funziona solo dopo aver navigato
                # alla prima pagina. Navigiamo a una URL fittizia prima.

            self._page = self._context.new_page()

            # Naviga alla home del portale prima (per attivare i cookie)
            self._page.goto(
                "https://portale.agenziaentrate.gov.it/PortaleWeb/home",
                wait_until="networkidle", timeout=30000,
            )
            self._page.wait_for_timeout(2000)

            # Se necessario, naviga a cons-web (dovrebbe funzionare con i cookie)
            self._navigate_to_cons_web()

            self._initialized = True
            self.logger("Corrispettivi browser inizializzato da requests.")
            return {"esito": "OK", "metodo": "requests_cookies"}

        except Exception as e:
            self.close()
            raise CorrispettiviBrowserError(
                f"Init da requests fallito: {e}"
            )

    def _sso_login(self, cf: str, pin: str, password: str):
        """Login SSO identico a CassettoFiscaleBrowserEngine."""
        self.logger(f"Login SSO per {cf}...")
        page = self._page

        page.goto(IAMPE_LOGIN_URL, wait_until="networkidle", timeout=60000)
        self.logger(f"  Pagina login caricata: {page.title()}")

        tab = page.locator(LOGIN_SELECTORS["tab_fisconline"]).first
        if tab.is_visible():
            tab.click()
            page.wait_for_timeout(1000)
            self.logger("  Tab Fisconline/Entratel cliccato.")

        cf_input = page.locator(LOGIN_SELECTORS["cf_input"]).first
        if not cf_input.is_visible():
            self.logger("  Già autenticato, salto login.")
            return

        cf_input.fill(cf)
        time.sleep(0.3)
        pw_input = page.locator(LOGIN_SELECTORS["password_input"]).first
        pw_input.fill(password)
        time.sleep(0.3)
        pin_input = page.locator(LOGIN_SELECTORS["pin_input"]).first
        pin_input.fill(pin)
        time.sleep(0.3)

        submit_btn = page.locator(LOGIN_SELECTORS["submit_btn"]).first
        if submit_btn.is_visible():
            submit_btn.click()
        else:
            page.keyboard.press("Enter")

        self.logger("  Attendo redirect post-login...")
        try:
            page.wait_for_url(
                lambda url: url.startswith("https://portale.agenziaentrate.gov.it"),
                timeout=30000,
            )
            self.logger(f"  Redirect a: {page.url[:80]}")
            page.wait_for_timeout(2000)
        except PwTimeout:
            self.logger("  Redirect non intercettato, procedo comunque.")

    def get_requests_session(self) -> "requests.Session":
        """
        Dopo init_session (login SSO riuscito), estrae i cookie dal
        contesto Playwright e crea una requests.Session autenticata.

        Usa WebView2 per login, poi cookies della sessione browser
        per chiamate API dirette.
        """
        import requests
        s = requests.Session()

        if self._context is None:
            raise CorrispettiviBrowserError("Chiamare init_session prima.")

        # Estrai cookie dal browser context e impostali nella session
        for c in self._context.cookies():
            s.cookies.set(c["name"], c["value"],
                          domain=c.get("domain", ""),
                          path=c.get("path", "/"))
        return s

    def _navigate_to_cons_web(self):
        """Clicca il link Fatture e Corrispettivi sulla home del portale.

        La home del portale ha un JavaScript che gestisce la catena
        di redirect SAML + initPortale + x-red per arrivare a ivaservizi.
        Cliccando il link, usiamo QUEL JavaScript (che ha i cookie giusti).
        """
        page = self._page
        self.logger("Cerco link Fatture e Corrispettivi sulla home...")

        # Cerca il link con click via JavaScript (il portale usa
        # gestori onclick/ng-click personalizzati)
        try:
            trovato = page.evaluate("""() => {
                // Cerca per testo visibile
                const links = document.querySelectorAll('a, button, div[role=button], span, .card, [class*=servizio]');
                for (const el of links) {
                    const text = el.textContent.trim().toLowerCase();
                    if (text.includes('fatture') && text.includes('corrispettivi')) {
                        el.click();
                        return el.textContent.trim();
                    }
                }
                // Cerca per attributi
                for (const sel of ['[href*="FATBTB"]', '[onclick*="FATBTB"]', '[ng-click*="fatture"]']) {
                    const el = document.querySelector(sel);
                    if (el) { el.click(); return sel; }
                }
                return '';
            }""")

            if trovato:
                self.logger(f"  Cliccato link: {trovato}")
                page.wait_for_timeout(5000)
                page.wait_for_load_state("networkidle", timeout=30000)
                self.logger(f"  Dopo click: {page.title()} — {page.url[:80]}")
                if "ivaservizi" in page.url:
                    self.logger("  ✅ Su ivaservizi!")
                    return
            else:
                self.logger("  Nessun link Fatture trovato sulla home")
                # Salva HTML per debug
                with open("/tmp/portale_home.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
        except Exception as e:
            self.logger(f"  Click Fatture fallito: {e}")
            # Salva HTML per debug
            with open("/tmp/portale_home.html", "w", encoding="utf-8") as f:
                f.write(page.content())

    def fetch_dettaglio_linee(
        self, id_invio: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Recupera le righe del dettaglio per un singolo corrispettivo.

        Flusso SPA (come da portale):
          1. Naviga a cons-web/#/corrispettivi/puntuale
          2. Inserisce idInvio in "identificativo trasmissione"
          3. Seleziona "Altri" come tipo corrispettivo
          4. Clicca "Cerca"
          5. Legge la tabella "Dati contabili del corrispettivo"

        Returns:
            Lista di dict: [{"aliquotaIva": 22, "imponibile": ..., "imposta": ...}]
        """
        if not self._initialized or self._page is None:
            raise CorrispettiviBrowserError(
                "Chiamare init_session() prima di fetch_dettaglio_linee()."
            )

        page = self._page

        # Naviga all'hash route della ricerca puntuale.
        # init_session ha gia' chiamato _navigate_to_cons_web,
        # quindi siamo su cons-web (o abbiamo fallito).
        if "ivaservizi" not in page.url:
            self.logger("  ⚠️ Non su ivaservizi — navigazione fallita")
            with open(f"/tmp/corrispettivi_spa_{id_invio}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            return None

        # Imposta l'hash route per la ricerca puntuale
        self.logger(f"  Navigo a {CONS_WEB_SPA}...")
        try:
            page.goto(CONS_WEB_SPA, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            self.logger(f"  SPA: {page.title()} — {page.url[:80]}")
        except Exception as e:
            self.logger(f"  Hash route fallito: {e}, URL: {page.url[:80]}")

        # 2. Inserisci ID Invio nel campo "identificativo trasmissione"
        try:
            # Cerca il campo input per id invio
            id_input = page.locator("input").first
            # Prova vari selettori: name, placeholder, label vicino
            for selector in [
                "input[placeholder*='invio']",
                "input[placeholder*='Invio']",
                "input[placeholder*='identificativo']",
                "input[name*='invio']",
                "input[formcontrolname*='invio']",
                "input",
            ]:
                el = page.locator(selector).first
                if el.is_visible():
                    el.click()
                    el.fill(str(id_invio))
                    self.logger(f"  Inserito ID Invio: {id_invio} nel campo {selector}")
                    page.wait_for_timeout(500)
                    break
        except Exception as e:
            self.logger(f"  Inserimento ID Invio fallito: {e}")
            # Salva HTML per debug
            with open(f"/tmp/corrispettivi_spa_{id_invio}_noinput.html", "w", encoding="utf-8") as f:
                f.write(page.content())

        # 3. Cambia il tipo di corrispettivo se necessario
        try:
            # Cerca una select/dropdown
            select = page.locator("select").first
            if select.is_visible():
                # Prova valori comuni
                for val in ["ALTRI", "Altri", "TUTTI", "Tutti"]:
                    try:
                        select.select_option(val)
                        self.logger(f"  Selezionato tipo: {val}")
                        page.wait_for_timeout(500)
                        break
                    except Exception:
                        continue
        except Exception:
            pass

        # 4. Clicca "Cerca"
        try:
            for btn_selector in [
                "button:has-text('Cerca')",
                "button:has-text('cerca')",
                "button[type='submit']",
                "input[type='submit']",
            ]:
                btn = page.locator(btn_selector).first
                if btn.is_visible():
                    btn.click()
                    self.logger("  Cliccato 'Cerca'")
                    page.wait_for_timeout(3000)
                    break
        except Exception as e:
            self.logger(f"  Click Cerca fallito: {e}")

        # 5. Attendi che la tabella si carichi
        try:
            page.wait_for_selector(
                "table, .table, [class*='tabella'], [class*='dettaglio']",
                timeout=15000,
            )
            page.wait_for_timeout(1000)
        except Exception:
            self.logger("  Tabella non trovata, attendo comunque...")
            page.wait_for_timeout(3000)

        # 6. Leggi l'HTML e parsifica la tabella
        html_content = page.content()

        # Salva per debug
        debug_path = f"/tmp/corrispettivi_dettaglio_{id_invio}.html"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        self.logger(f"  HTML salvato in {debug_path}")

        linee = self._parse_tabella_linee(html_content)
        if linee:
            self.logger(f"  ✅ Trovate {len(linee)} righe per {id_invio}")
        else:
            self.logger(f"  ⚠️ Nessuna tabella trovata per {id_invio}")

        return linee

    def _parse_tabella_linee(
        self, html_content: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Cerca nell'HTML la tabella "Dati contabili del corrispettivo"
        ed estrae le righe con Aliquota IVA, Imponibile, Imposta, Natura.
        """
        try:
            tree = lxml_html.fromstring(html_content)
        except Exception:
            return None

        # Cerca la tabella con intestazioni "Aliquota IVA" o "Numero linea"
        tables = tree.xpath(
            "//table[contains(., 'Aliquota IVA') or "
            "contains(., 'Numero linea') or "
            "contains(@class, 'corrispettivo') or "
            "contains(@class, 'dettaglio')]"
        )
        if not tables:
            # Fallback: cerca qualunque tabella con righe (th + td)
            tables = tree.xpath("//table[tr/th and tr/td]")

        if not tables:
            return None

        linee: List[Dict[str, Any]] = []
        for table in tables[:1]:
            rows = table.xpath(".//tr")
            for row in rows:
                cells = row.xpath("td | th")
                if len(cells) < 2:
                    continue

                testo_completo = " ".join(
                    c.text_content().strip() for c in cells
                ).lower()

                # Salta righe di intestazione o totali
                if any(
                    w in testo_completo
                    for w in ["aliquota iva", "numero linea", "totale", "descrizione"]
                ):
                    continue

                # Cerca aliquota IVA nella cella
                aliquota = None
                imponibile = None
                imposta = None
                natura = ""
                for cell in cells:
                    text = cell.text_content().strip()
                    text_lower = text.lower()
                    # Aliquota: match "22.00 %" o "22%" o "0.00 %"
                    ali_match = re.search(r'(\d+)[\.,]\d+\s*%', text)
                    if ali_match:
                        val = ali_match.group(1)
                        if val == "0":
                            aliquota = 0
                        else:
                            aliquota = int(val)
                    # Imponibile: numero con "€"
                    elif "€" in text and "imponibile" not in text_lower:
                        num_match = re.search(r'([\d\.,]+)\s*€', text)
                        if num_match:
                            try:
                                importo_str = num_match.group(1).replace(".", "").replace(",", ".")
                                imponibile = float(importo_str)
                            except ValueError:
                                pass
                    # Natura
                    elif "regime" in text_lower or "margine" in text_lower or "esente" in text_lower:
                        natura = text

                if aliquota is not None and imponibile is not None:
                    linee.append({
                        "aliquotaIva": aliquota,
                        "imponibile": str(imponibile),
                        "imposta": str(round(imponibile * aliquota / 100, 2)) if aliquota > 0 else "0",
                        "natura": natura,
                    })

        return linee if linee else None


# ─── Funzione di utilità per uso diretto ─────────────────────────────────────


def fetch_dettaglio_linee(
    cf: str,
    pin: str,
    password: str,
    id_invio: str,
    logger: Callable[..., None],
) -> Optional[List[Dict[str, Any]]]:
    """
    Funzione di utilità: login SSO + recupero righe dettaglio corrispettivo.
    """
    engine = CorrispettiviBrowserEngine(logger, headless=True)
    try:
        engine.init_session(cf, pin, password)
        return engine.fetch_dettaglio_linee(id_invio)
    except Exception as e:
        logger(f"Errore dettaglio corrispettivo: {e}")
        return None
    finally:
        engine.close()


__all__ = [
    "CorrispettiviBrowserEngine",
    "CorrispettiviBrowserError",
    "PLAYWRIGHT_AVAILABLE",
    "fetch_dettaglio_linee",
]
