"""
Cassetto Fiscale via Playwright (browser automation).

Usa un browser headless Chromium per l'inizializzazione della sessione
Cassetto Fiscale, superando i limiti delle richieste HTTP semplici
(HTTP 409 su initCassetto / cambiaCliente).

Flusso:
  1. Login SSO via browser (inserisce CF, PIN, password)
  2. Navigazione al Cassetto Fiscale
  3. Inizializzazione sessione (initLight, initCassetto)
  4. Cambio cliente (per intermediari)
  5. Query servlet documenti e parsing HTML
  6. Download PDF

Cross-platform: macOS (dev) e Windows (produzione).
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from app.cassetto_fiscale_engine import (
    CASSETTO_BASE,
    CASSETTO_SERVLET,
    CASSETTO_REST,
    CASSETTO_HOME,
    TIPI_DOCUMENTO,
    TIPO_TO_RIC,
    export_json,
    CassettoFiscaleError,
)

# ─── Tentativo import Playwright (fallback graceful se non installato) ────────

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


# ─── Costanti ──────────────────────────────────────────────────────────────────

# URL di login SSO corretto per l'Agenzia delle Entrate (IAM Portale Entrate)
IAMPE_LOGIN_URL = (
    "https://iampe.agenziaentrate.gov.it/sam/UI/Login"
    "?realm=/agenziaentrate"
    "&goto=https%3A%2F%2Fportale.agenziaentrate.gov.it%3A443%2FPortaleWeb%2Fhome"
)

# Selettori per la nuova pagina di login (React, tab-based)
LOGIN_SELECTORS = {
    "tab_fisconline": "text=Fisconline/Entratel",
    "cf_input": "#username-fo-ent",
    "password_input": "#password-fo-ent-1",
    "pin_input": "#pin-fo-ent",
    "submit_btn": "button:has-text('Accedi')",
}


class CassettoFiscaleBrowserEngine:
    """
    Motore Cassetto Fiscale basato su Playwright.

    Usa un browser headless Chromium per autenticazione SSO e navigazione
    del Cassetto Fiscale, restituendo HTML parsabile dalle stesse funzioni
    di ``cassetto_fiscale_engine.py``.

    L'interfaccia è compatibile con ``CassettoFiscaleEngine``:
      - ``init_session(cf, pin, password)``
      - ``cambia_cliente(piva)``
      - ``navigate_servlet(ric, anno, piva)``
      - ``fetch_document_list(tipo, anno, piva)``
      - ``download_document(url, output_dir, filename)``
    """

    def __init__(
        self,
        logger: Callable[..., None],
        headless: bool = True,
        browser_type: str = "chromium",
    ):
        """
        Args:
            logger: Funzione di logging.
            headless: Se True, browser in modalità headless (senza UI).
            browser_type: "chromium" (default), "firefox" o "webkit".
        """
        self.logger = logger
        self.headless = headless
        self.browser_type = browser_type
        self._initialized = False
        self._current_piva: Optional[str] = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._download_dir: Optional[str] = None

    # ── Proprietà ─────────────────────────────────────────────────────────────

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def chiave_cassetto(self) -> Optional[str]:
        return None  # Playwright non usa chiave esplicita

    @property
    def page(self):
        return self._page

    # ── Login SSO via browser ────────────────────────────────────────────────

    def init_session(
        self,
        cf: str,
        pin: str,
        password: str,
    ) -> Dict[str, Any]:
        """
        Avvia il browser, esegue il login SSO Agenzia Entrate e inizializza
        il Cassetto Fiscale.

        Args:
            cf: Codice fiscale dell'utente.
            pin: PIN Entratel/Fisconline.
            password: Password.

        Returns:
            Dict con esito dell'operazione.
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise CassettoFiscaleError(
                "Playwright non installato. Esegui: pip install playwright && "
                "python -m playwright install chromium"
            )

        self.logger("Avvio browser per login SSO...")

        try:
            from playwright.sync_api import sync_playwright

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

            # Step 1: Login SSO
            self._sso_login(cf, pin, password)

            # Step 2: Naviga a Cassetto Fiscale
            self._navigate_to_cassetto()

            # Step 3: Inizializza sessione Cassetto
            self._init_cassetto_session()

            self._initialized = True
            self.logger("Cassetto Fiscale inizializzato via browser.")
            return {"esito": "OK", "metodo": "playwright"}

        except Exception as e:
            self.close()
            raise CassettoFiscaleError(
                f"Init Cassetto Fiscale via browser fallito: {e}"
            )

    def _sso_login(self, cf: str, pin: str, password: str):
        """
        Esegue il login SSO sul nuovo portale Agenzia Entrate (IAMPE).

        Flusso:
          1. Naviga a iampe.agenziaentrate.gov.it
          2. Clicca tab "Fisconline/Entratel"
          3. Compila CF, password, PIN
          4. Clicca "Accedi"
          5. Attende redirect al portale
        """
        self.logger(f"Login SSO per {cf}...")
        page = self._page

        # Step 1: Vai alla pagina di login
        page.goto(IAMPE_LOGIN_URL, wait_until="networkidle", timeout=60000)
        self.logger(f"  Pagina login caricata: {page.title()}")

        # Step 2: Clicca tab "Fisconline/Entratel"
        tab = page.locator(LOGIN_SELECTORS["tab_fisconline"]).first
        if tab.is_visible():
            tab.click()
            page.wait_for_timeout(1000)
            self.logger("  Tab Fisconline/Entratel cliccato.")
        else:
            self.logger("  Tab Fisconline/Entratel non trovato, procedo direttamente.")

        # Step 3: Verifica se siamo già autenticati
        cf_input = page.locator(LOGIN_SELECTORS["cf_input"]).first
        if not cf_input.is_visible():
            self.logger("  Già autenticato, salto login.")
            return

        # Step 4: Compila credenziali
        cf_input.fill(cf)
        time.sleep(0.3)
        pw_input = page.locator(LOGIN_SELECTORS["password_input"]).first
        pw_input.fill(password)
        time.sleep(0.3)
        pin_input = page.locator(LOGIN_SELECTORS["pin_input"]).first
        pin_input.fill(pin)
        time.sleep(0.3)

        # Step 5: Clicca "Accedi"
        submit_btn = page.locator(LOGIN_SELECTORS["submit_btn"]).first
        if submit_btn.is_visible():
            submit_btn.click()
        else:
            # Fallback: premi Enter
            page.keyboard.press("Enter")

        # Step 6: Attendi redirect (fino a 30s)
        self.logger("  Attendo redirect post-login...")
        try:
            page.wait_for_url(
                lambda url: url.startswith("https://portale.agenziaentrate.gov.it"),
                timeout=30000,
            )
            self.logger(f"  Redirect a: {page.url[:80]}")
            # Piccola attesa per assicurarsi che i cookie di sessione siano impostati
            page.wait_for_timeout(2000)
        except Exception:
            self.logger(f"  Redirect non rilevato, URL: {page.url[:80]}")
            # Potrebbe essere già autenticato o il submit non è andato a buon fine
            self.logger("  Provo attendere 5 secondi e verifico...")
            page.wait_for_timeout(5000)
            if "portale.agenziaentrate.gov.it" in page.url:
                self.logger("  Login rilevato dopo attesa!")
            else:
                self.logger("  ATTENZIONE: login potrebbe non essere riuscito")

    def _navigate_to_cassetto(self):
        """Naviga al Cassetto Fiscale."""
        self.logger("Navigazione al Cassetto Fiscale...")
        page = self._page

        try:
            page.goto(CASSETTO_HOME, wait_until="domcontentloaded", timeout=30000)
            # Attendi che la pagina sia stabile
            page.wait_for_load_state("networkidle", timeout=30000)
            self.logger(f"  Cassetto Home: {page.title()}")
        except Exception as e:
            self.logger(f"  Navigazione Cassetto: {e}")
            # Prova navigazione diretta senza wait_until
            try:
                page.goto(CASSETTO_HOME, timeout=30000)
                page.wait_for_timeout(2000)
                self.logger(f"  Cassetto Home (retry): {page.title()}")
            except Exception as e2:
                self.logger(f"  Navigazione Cassetto (retry): {e2}")

    def _init_cassetto_session(self):
        """Chiama initLight e initCassetto via browser fetch."""
        page = self._page
        ts = int(time.time() * 1000)

        # initLight
        self.logger("InitLight...")
        try:
            result = page.evaluate(
                """
                async () => {
                    const r = await fetch(
                        'https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initLight?v=' + Date.now()
                    );
                    return {status: r.status, body: await r.text()};
                }
            """
            )
            self.logger(f"  InitLight: HTTP {result['status']}")
            if result["status"] not in (200, 204, 409):
                self.logger(
                    f"  InitLight: HTTP {result['status']} — {result['body'][:200]}"
                )
        except Exception as e:
            self.logger(f"  InitLight: {e} (non bloccante)")

        # initCassetto via POST (fetch JS)
        self.logger("InitCassetto...")
        try:
            result = page.evaluate(
                """
                async () => {
                    const ts = Date.now();
                    const r = await fetch(
                        'https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initCassetto?v=' + ts,
                        {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: '{}'
                        }
                    );
                    return {status: r.status, body: await r.text()};
                }
            """
            )
            self.logger(f"  InitCassetto: HTTP {result['status']}")
            if result["status"] == 200:
                try:
                    data = json.loads(result["body"])
                    self.logger(f"  InitCassetto OK: {json.dumps(data)[:100]}")
                except json.JSONDecodeError:
                    self.logger(f"  InitCassetto body: {result['body'][:100]}")
            elif result["status"] == 409:
                self.logger("  InitCassetto: 409 (sessione già attiva)")
            elif result["status"] not in (200, 204, 409):
                self.logger(
                    f"  InitCassetto: HTTP {result['status']} — {result['body'][:200]}"
                )
        except Exception as e:
            self.logger(f"  InitCassetto via fetch fallito: {e}")
            # Fallback
            self._js_post_init()

    def _js_post_init(self):
        """Fallback initCassetto via JavaScript fetch con POST."""
        page = self._page
        try:
            result = page.evaluate(
                """
                async () => {
                    const ts = Date.now();
                    const url = 'https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initCassetto?v=' + ts;
                    try {
                        const r = await fetch(url, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: '{}'
                        });
                        return {status: r.status, body: await r.text()};
                    } catch(e) {
                        return {status: 0, body: e.message};
                    }
                }
            """
            )
            self.logger(f"  InitCassetto (retry): HTTP {result['status']}")
        except Exception as e:
            self.logger(f"  InitCassetto retry fallito: {e}")

    # ── Cambio cliente via PortaleWeb ───────────────────────────────────────

    def _cambia_cliente_via_rest(self, piva: str, use_cf: bool = False) -> Dict[str, Any]:
        """
        Cambia cliente attivo via REST API ``cambiaCliente`` eseguita
        nel contesto del browser (eredita i cookie di sessione e l'header ``x-appl``).

        Usa lo stesso formato della web app SPA (cf + pin opzionale).
        Dopo la risposta HTTP 200, verifica che il cambio sia effettivo
        chiamando ``/rs/home`` e controllando ``cfContribuente``.

        Args:
            piva: P.IVA o CF del cliente (convertito a uppercase).
            use_cf: Se True, usa ``{"cf": piva}`` invece di ``{"pIva": piva}``.

        Returns:
            Dict con esito.

        Raises:
            CassettoFiscaleError: se la REST API fallisce o il cambio non è effettivo.
        """
        page = self._page
        piva = piva.upper()  # CF/P.IVA sono case-insensitive
        body_key = "cf" if use_cf else "pIva"
        self.logger(f"Cambio cliente via REST: {{{body_key}: {piva}}}...")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                result = page.evaluate(
                    """
                    async (params) => {
                        const ts = Date.now();
                        const body = {};
                        body[params.key] = params.val;
                        // L'header x-appl è richiesto (token di correlazione sessione)
                        let appl = localStorage.getItem('x-appl') || '';
                        const r = await fetch(
                            'https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/cambiaCliente?v=' + ts,
                            {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                    'x-appl': appl
                                },
                                body: JSON.stringify(body)
                            }
                        );
                        const text = await r.text();
                        let data = null;
                        try { data = JSON.parse(text); } catch(e) {}
                        // Se il server rimanda un nuovo x-appl in risposta, salvalo
                        const newAppl = r.headers.get('x-appl');
                        if (newAppl) localStorage.setItem('x-appl', newAppl);
                        return {status: r.status, body: text, data: data};
                    }
                    """,
                    {"key": body_key, "val": piva},
                )

                status = result["status"]
                body_text = result.get("body", "")
                data = result.get("data")
                self.logger(f"  REST cambiaCliente (tentativo {attempt}): HTTP {status} — {body_text[:150]}")

                if status in (200, 204):
                    # Verifica che il cambio sia effettivo chiamando /rs/home
                    try:
                        home_check = page.evaluate(
                            """
                            async () => {
                                const appl = localStorage.getItem('x-appl') || '';
                                const r = await fetch('/casshome-rest/rs/home?v=' + Date.now(), {headers: {'x-appl': appl}});
                                if (r.status === 200) return await r.json();
                                return null;
                            }
                        """
                        )
                        if home_check and home_check.get("cfContribuente") == piva:
                            self._current_piva = piva
                            self.logger(f"  ✅ Cliente cambiato a {piva} via REST (verificato).")
                            return {"esito": "OK", "metodo": "rest"}
                        else:
                            cf_attuale = (home_check or {}).get("cfContribuente", "?")
                            self.logger(f"  ⚠️ REST 200 ma cfContribuente è {cf_attuale} (atteso {piva}) — considero fallito")
                            raise CassettoFiscaleError(
                                f"REST 200 ma sessione non cambiata: cfContribuente={cf_attuale}"
                            )
                    except CassettoFiscaleError:
                        raise
                    except Exception as ve:
                        self.logger(f"  Verifica cambio fallita: {ve}, procedo comunque.")
                        self._current_piva = piva
                        return {"esito": "OK", "metodo": "rest"}

                if status == 409:
                    msg = data.get("messaggio", body_text[:300]) if data else body_text[:300]
                    self.logger(f"  409 — messaggio: {msg}")
                    if attempt < max_attempts:
                        self.logger(f"  Riprovo con nuovo x-appl...")
                        page.wait_for_timeout(1000)
                        continue
                    raise CassettoFiscaleError(
                        f"Cambio cliente 409 dopo {max_attempts} tentativi con "
                        f"{{{body_key}: {piva}}}: {msg}"
                    )

                raise CassettoFiscaleError(
                    f"Cambio cliente: HTTP {status} ({body_text[:200]})"
                )

            except CassettoFiscaleError:
                raise
            except Exception as e:
                raise CassettoFiscaleError(f"REST cambiaCliente fallito: {e}")

        raise CassettoFiscaleError(
            f"Cambio cliente fallito dopo {max_attempts} tentativi"
        )

    def _cambia_cliente_via_mobx_store(self, cf: str) -> Dict[str, Any]:
        """
        Cambia cliente chiamando direttamente la MobX store ``homeStore.setCliente``
        nella SPA del Cassetto Fiscale Home.

        Questo replica esattamente il comportamento del pulsante
        "Cassetto del contribuente delegante" → inserimento CF → "Vai".

        Args:
            cf: Codice fiscale del cliente delegante.

        Returns:
            Dict con esito.

        Raises:
            CassettoFiscaleError: se il cambio fallisce.
        """
        page = self._page
        self.logger(f"Cambio cliente via MobX store (cf={cf})...")

        try:
            result = page.evaluate(
                """
                async (cf) => {
                    // 1) Verifica che la store sia accessibile
                    if (typeof window.__ROOT_STORE__ === 'undefined' &&
                        typeof window.__mobxRootStore === 'undefined') {
                        // Cerca nel contesto React
                        const root = document.getElementById('root');
                        if (!root || !root._reactRootContainer) {
                            return {success: false, error: 'Store non trovato'};
                        }
                    }

                    // Prova a trovare rootStore in vari modi
                    let rootStore = window.__ROOT_STORE__ || window.__mobxRootStore;

                    if (!rootStore && window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
                        // Fallback: cerca il primo store con homeStore
                        for (const key of Object.keys(window)) {
                            if (window[key] && window[key].homeStore) {
                                rootStore = window[key];
                                break;
                            }
                        }
                    }

                    if (!rootStore || !rootStore.homeStore) {
                        return {success: false, error: 'homeStore non trovata nel contesto globale'};
                    }

                    // 2) Prepara oggetto cliente come fa DelegaPanel
                    const cliente = {cf: cf};

                    // 3) Actions mock (setFieldError logga errore)
                    const actions = {
                        setFieldError: (field, msg) => console.error('setFieldError:', field, msg)
                    };

                    // 4) Se il cf è lo stesso di quello corrente, non serve cambiare
                    if (rootStore.homeStore.userCassetto &&
                        rootStore.homeStore.userCassetto.cfContribuente === cf) {
                        return {success: true, alreadySame: true, message: 'Già nel cassetto del cliente ' + cf};
                    }

                    // 5) Chiama setCliente — è @action quindi wrappa in async
                    await rootStore.homeStore.setCliente(cliente, actions);

                    // 6) Verifica il risultato
                    const message = rootStore.homeStore.messageCambio || '';
                    return {
                        success: message.includes(cf),
                        message: message,
                        cfContribuente: rootStore.homeStore.userCassetto?.cfContribuente
                    };
                }
                """,
                cf,
            )

            self.logger(f"  Result MobX: {json.dumps(result, default=str)[:200]}")

            if result.get("success"):
                if result.get("alreadySame"):
                    self.logger(f"  ⚠️ Già nel cassetto del cliente {cf}, nessun cambio necessario.")
                else:
                    self.logger(f"  ✅ Cliente cambiato a {cf} via MobX store.")
                self._current_piva = cf
                # Attendi il refresh della store
                page.wait_for_timeout(2000)
                return {"esito": "OK", "metodo": "mobx_store"}

            error = result.get("error", result.get("message", "sconosciuto"))
            self.logger(f"  ❌ MobX store fallito: {error}")
            raise CassettoFiscaleError(f"Cambio via MobX store fallito: {error}")

        except CassettoFiscaleError:
            raise
        except Exception as e:
            raise CassettoFiscaleError(f"Cambio via MobX store: {e}")

    def _debug_home_state(self) -> Dict[str, Any]:
        """
        Chiama ``/casshome-rest/rs/home`` per ispezionare lo stato corrente
        del Cassetto Fiscale (flag abilitazioni, cfContribuente, etc.).

        Returns:
            Dict con la risposta JSON del server.
        """
        page = self._page
        try:
            result = page.evaluate(
                """
                async () => {
                    const appl = localStorage.getItem('x-appl') || '';
                    const r = await fetch(
                        '/casshome-rest/rs/home?v=' + Date.now(),
                        {headers: {'x-appl': appl}}
                    );
                    return {status: r.status, body: await r.text()};
                }
            """
            )
            if result["status"] == 200:
                data = json.loads(result["body"])
                self.logger(f"  /rs/home: OK — {json.dumps(data, default=str)[:500]}")
                return data
            else:
                self.logger(f"  /rs/home: HTTP {result['status']} — {result['body'][:200]}")
                return {"error": f"HTTP {result['status']}"}
        except Exception as e:
            self.logger(f"  /rs/home fallito: {e}")
            return {"error": str(e)}

    def _cambia_cliente_via_spa_ui(self, cf: str) -> Dict[str, Any]:
        """
        Usa la UI della SPA Cassetto Fiscale Home per cambiare cliente:
          1. naviga a ``/CassHomeWeb/``
          2. clicca "Cassetto del contribuente delegante"
          3. inserisce CF nel form
          4. clicca "Vai"

        Se il bottone non è visibile, prova a chiamare ``showDelega(true)``
        direttamente sulla MobX store.

        Args:
            cf: CF del cliente delegante.

        Returns:
            Dict con esito.

        Raises:
            CassettoFiscaleError: se il cambio fallisce.
        """
        page = self._page
        self.logger(f"Cambio cliente via SPA UI (cf={cf})...")

        try:
            # 1) Naviga alla HOME della SPA se non già presente
            if "/CassHomeWeb/" not in page.url:
                page.goto(
                    "https://cassetto.agenziaentrate.gov.it/CassHomeWeb/",
                    wait_until="networkidle",
                    timeout=30000,
                )
                page.wait_for_timeout(3000)

            # 2) Cerca il bottone "Cassetto del contribuente delegante"
            delega_btn = page.query_selector(
                "button:has-text('Cassetto del contribuente delegante')"
            )

            if not delega_btn:
                # Non visibile — forse cassettoDelegato è false
                # Prova a chiamare showDelega(true) direttamente sulla store
                self.logger("  Bottone non visibile, provo showDelega(true) via store...")
                store_result = page.evaluate(
                    """
                    async () => {
                        let store = null;
                        // Cerca rootStore in vari posti
                        for (const k of Object.keys(window)) {
                            if (window[k] && window[k].homeStore) {
                                store = window[k];
                                break;
                            }
                        }
                        if (!store) {
                            // Prova a traversare da React
                            const root = document.getElementById('root');
                            if (root && root._reactRootContainer) {
                                // Non facile da traversare senza React internals
                            }
                            return {success: false, error: 'Store non trovato'};
                        }
                        store.homeStore.showDelega(true);
                        return {success: true};
                    }
                """
                )
                self.logger(f"  showDelega result: {store_result}")
                page.wait_for_timeout(1000)

                # Ora verifica se il form è apparso
                delega_btn = page.query_selector(
                    "button:has-text('Cassetto del contribuente delegante')"
                )
                if not delega_btn:
                    self.logger("  Bottone ancora non visibile, provo MobX setCliente directly...")
                    # Fallback diretto a MobX store
                    return self._cambia_cliente_via_mobx_store(cf)

            # 3) Click sul bottone per aprire il form
            if delega_btn:
                self.logger("  Clicko 'Cassetto del contribuente delegante'...")
                delega_btn.click()
                page.wait_for_timeout(1000)

            # 4) Trova l'input CF nel form
            cf_input = page.query_selector("input[name='cf']")
            if not cf_input:
                cf_input = page.query_selector("#cf")
            if not cf_input:
                cf_input = page.query_selector("input[placeholder*='fiscale']")

            if not cf_input:
                raise CassettoFiscaleError(
                    "Input CF non trovato nel form delega della SPA"
                )

            self.logger(f"  Inserisco CF: {cf}...")
            cf_input.fill(cf)
            page.wait_for_timeout(500)

            # 5) Clicca "Vai"
            vai_btn = page.query_selector("button:has-text('Vai')")
            if not vai_btn:
                vai_btn = page.query_selector("button[type='submit']")

            if not vai_btn:
                raise CassettoFiscaleError(
                    "Pulsante 'Vai' non trovato nel form delega"
                )

            self.logger("  Clicko 'Vai'...")
            vai_btn.click()
            page.wait_for_timeout(3000)

            # 6) Verifica cambio avvenuto
            # Controlla il messaggio di conferma nella store
            check = page.evaluate(
                """
                async () => {
                    let store = null;
                    for (const k of Object.keys(window)) {
                        if (window[k] && window[k].homeStore) {
                            store = window[k];
                            break;
                        }
                    }
                    if (store) {
                        return {
                            messageCambio: store.homeStore.messageCambio || '',
                            cfContribuente: store.homeStore.userCassetto?.cfContribuente
                        };
                    }
                    return {messageCambio: '', cfContribuente: null};
                }
            """
            )
            self.logger(f"  Post-cambio stato: {json.dumps(check, default=str)[:200]}")

            if cf in str(check.get("messageCambio", "")) or \
               check.get("cfContribuente") == cf:
                self._current_piva = cf
                self.logger(f"  ✅ Cliente cambiato a {cf} via SPA UI.")
                return {"esito": "OK", "metodo": "spa_ui"}
            else:
                self.logger(f"  ⚠️ Cambio eseguito ma non verificato, procedo.")
                self._current_piva = cf
                return {"esito": "OK", "metodo": "spa_ui"}

        except CassettoFiscaleError:
            raise
        except Exception as e:
            raise CassettoFiscaleError(f"Cambio via SPA UI fallito: {e}")

    def _conferma_cambiautenza(self, piva: str) -> None:
        """
        Clicca 'Conferma' su cambiautenza, attende redirect,
        ripristina sessione Cassetto e aggiorna ``_current_piva``.
        """
        page = self._page
        confirm_btn = page.query_selector(
            "button:has-text('Conferma'), input[type='submit']"
        )
        if not confirm_btn:
            raise CassettoFiscaleError(
                "Pulsante 'Conferma' non trovato su cambiautenza."
            )
        confirm_btn.click()
        page.wait_for_timeout(3000)
        self.logger(f"  Confermato, redirect a: {page.url}")

        self._restore_cassetto_session()
        self._current_piva = piva
        self.logger(f"  ✅ Cliente cambiato a {piva} via PortaleWeb.")

    def cambia_cliente(self, piva: str) -> Dict[str, Any]:
        """
        Cambia cliente attivo — tenta:
          1. REST API con ``{"pIva": piva}`` + header ``x-appl``
          2. REST API con ``{"cf": piva}`` + header ``x-appl``
          3. MobX store ``homeStore.setCliente`` (replica il click "Cassetto del contribuente delegante")
          4. SPA UI (click "Cassetto del contribuente delegante" → inserisci CF → "Vai")
          5. PortaleWeb/cambiautenza (Incaricato dropdown o Persona di fiducia input CF)

        Args:
            piva: P.IVA o CF del cliente (case-insensitive, convertito a uppercase).

        Returns:
            Dict con esito.
        """
        piva = piva.upper().strip()

        if not self._initialized or self._page is None:
            raise CassettoFiscaleError(
                "Chiamare init_session() prima di cambia_cliente()."
            )

        # ── Tentativo 1: REST API con pIva ─────────────────────────────────
        try:
            return self._cambia_cliente_via_rest(piva, use_cf=False)
        except CassettoFiscaleError as e:
            self.logger(f"  REST pIva fallito: {e}")

        # ── Tentativo 2: REST API con cf ───────────────────────────────────
        try:
            return self._cambia_cliente_via_rest(piva, use_cf=True)
        except CassettoFiscaleError as e:
            self.logger(f"  REST cf fallito: {e}")

        # ── Tentativo 3: MobX store (replica UI Cassetto Fiscale Home) ──────
        try:
            return self._cambia_cliente_via_mobx_store(piva)
        except CassettoFiscaleError as e:
            self.logger(f"  MobX store fallito: {e}")

        # ── Tentativo 4: SPA UI (Cassetto del contribuente delegante) ───────
        try:
            return self._cambia_cliente_via_spa_ui(piva)
        except CassettoFiscaleError as e:
            self.logger(f"  SPA UI fallito: {e}")

        # ── Tentativo 5: PortaleWeb/cambiautenza (Incaricato / Delega Diretta) ──
        self.logger(f"Fallback: cambio cliente a {piva} via PortaleWeb...")
        page = self._page

        try:
            page.goto(
                "https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_timeout(1000)

            # Prova ogni radio button: "Incaricato", "Delega Diretta", etc.
            # per vedere quale dropdown contiene il cliente cercato
            for rb in page.query_selector_all("input[name='ruoloType']"):
                rid = rb.get_attribute("id")
                if not rid:
                    continue
                label = page.evaluate(
                    f'document.querySelector(\'label[for="{rid}"]\')?.innerText'
                ) or ""
                self.logger(f'  Radio trovato: "{label.strip()}"')

                # Seleziona questo radio
                if not rb.is_checked():
                    rb.click()
                    page.wait_for_timeout(800)

                # Controlla se c'è un dropdown (Incaricato) o un input CF (Persona di fiducia)
                select_el = page.query_selector("select[name='utenza']")
                input_cf_el = page.query_selector("input[name='inputCf']")

                if select_el:
                    # ── Caso dropdown (Incaricato) ──
                    for opt in select_el.query_selector_all("option"):
                        val = opt.get_attribute("value") or ""
                        if val == piva:
                            page.select_option("select[name='utenza']", piva)
                            page.wait_for_timeout(500)
                            self.logger(
                                f'  Trovato {piva} nel dropdown '
                                f'con radio "{label.strip()}".'
                            )
                            self._conferma_cambiautenza(piva)
                            return {"esito": "OK", "metodo": "portale_dropdown"}
                        else:
                            if val:
                                self.logger(
                                    f'    Dropdown "{label.strip()}": '
                                    f'opzione "{val}" non è "{piva}"'
                                )
                elif input_cf_el:
                    # ── Caso input CF (Persona di fiducia / Delega Diretta) ──
                    self.logger(
                        f'  Radio "{label.strip()}": input CF trovato, '
                        f"inserisco {piva}..."
                    )
                    input_cf_el.fill(piva)
                    page.wait_for_timeout(500)
                    self._conferma_cambiautenza(piva)
                    return {"esito": "OK", "metodo": "portale_inputcf"}
                else:
                    self.logger(
                        f'  Radio "{label.strip()}": né dropdown né input CF trovati.'
                    )

            # Nessun radio ha permesso il cambio
            raise CassettoFiscaleError(
                f"Cliente {piva} non trovato in nessun dropdown né input CF "
                f"su cambiautenza. Verifica la delega."
            )

        except CassettoFiscaleError:
            raise
        except Exception as e:
            raise CassettoFiscaleError(
                f"Cambio cliente via PortaleWeb fallito: {e}"
            )

    def _restore_cassetto_session(self):
        """
        Ripristina la sessione Cassetto Fiscale dopo un cambio utenza.

        Dopo il cambio su PortaleWeb, la servlet Cassetto mostra
        "Necessario ripristino" — bisogna cliccare "Indietro" e poi
        navigare a HOME per riattivare la sessione per il nuovo cliente.
        """
        self.logger("Ripristino sessione Cassetto Fiscale...")
        page = self._page

        # 1. Vai a HOME — potrebbe mostrare "Necessario ripristino"
        page.goto(
            "https://cassetto.agenziaentrate.gov.it/cassfisc-web/"
            "CassettoFiscaleServlet?Ric=HOME",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(1000)

        # 2. Se c'è "Necessario ripristino", clicca "Indietro"
        body_text = page.inner_text("body")
        if "Necessario ripristino" in body_text:
            self.logger('  Pagina "Necessario ripristino" — click "Indietro"...')
            indietro = page.query_selector(
                "a:has-text('Indietro'), button:has-text('Indietro')"
            )
            if indietro:
                indietro.click()
                page.wait_for_timeout(3000)
                self.logger(f"  Dopo Indietro: {page.url[:80]}")
            else:
                self.logger("  Link 'Indietro' non trovato, procedo.")

        # 3. Torna a HOME per sessione attiva
        page.goto(
            "https://cassetto.agenziaentrate.gov.it/cassfisc-web/"
            "CassettoFiscaleServlet?Ric=HOME",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(2000)
        self.logger(f"  HOME caricata: {page.title()}")

    # ── Navigazione servlet ──────────────────────────────────────────────────

    def navigate_servlet(
        self,
        ric: str,
        anno: Optional[int] = None,
        piva: Optional[str] = None,
        extra_params: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str]:
        """
        Naviga alla CassettoFiscaleServlet e restituisce l'HTML.

        Args:
            ric: Codice tipo documento (es. "F24", "RED", "UNI").
            anno: Anno di riferimento.
            piva: P.IVA cliente (per intermediari).
            extra_params: Parametri URL aggiuntivi.

        Returns:
            (status_code, html_text)
        """
        if not self._initialized or self._page is None:
            raise CassettoFiscaleError(
                "Chiamare init_session() prima di navigate_servlet()."
            )

        # Cambia cliente se necessario (CF/P.IVA case-insensitive)
        if piva and piva.upper() != (self._current_piva or "").upper():
            try:
                self.cambia_cliente(piva)
                # Il cambio via REST API aggiorna /casshome-rest ma la servlet
                # /cassfisc-web necessita di un refresh della sessione.
                # Naviga a HOME per forzare l'aggiornamento.
                self.logger("  Refresh sessione servlet dopo cambio cliente...")
                self._page.goto(
                    CASSETTO_SERVLET + "?Ric=HOME",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                self._page.wait_for_timeout(1000)
            except CassettoFiscaleError as e:
                self.logger(
                    f"  Cambio cliente non riuscito: {e}. "
                    f"Provo con parametri servlet..."
                )
                extra_params = dict(extra_params or {})
                extra_params.setdefault("cf", piva)
                extra_params.setdefault("pIva", piva)

        # Costruisci URL
        params_list = [("Ric", ric)]
        if anno is not None:
            params_list.append(("Anno", str(anno)))
        if extra_params:
            for k, v in extra_params.items():
                params_list.append((k, v))

        from urllib.parse import urlencode
        url = f"{CASSETTO_SERVLET}?{urlencode(params_list)}"

        self.logger(
            f"Navigazione servlet: Ric={ric} Anno={anno} P.IVA={piva or '-'}"
        )

        max_retry = 2
        for attempt in range(max_retry):
            try:
                resp = self._page.goto(
                    url, wait_until="networkidle", timeout=30000
                )
                status = resp.status if resp else 0
                html = self._page.content()

                # Se trova "Necessario ripristino", prova a ripristinare
                if (
                    "Necessario ripristino" in html
                    or "sessione di lavoro deve essere ripristinata" in html
                ):
                    if attempt < max_retry - 1:
                        self.logger(
                            "  Sessione da ripristinare — eseguo ripristino..."
                        )
                        self._restore_cassetto_session()
                        continue
                    else:
                        self.logger(
                            "  Sessione ancora da ripristinare dopo retry."
                        )

                return status, html
            except Exception as e:
                self.logger(f"  Navigazione servlet fallita (tentativo {attempt+1}): {e}")
                if attempt < max_retry - 1:
                    self.logger("  Riprovo...")
                    continue
                return 0, ""

    # ── Fetch document list ──────────────────────────────────────────────────

    def fetch_document_list(
        self,
        tipo: str,
        anno: int,
        piva: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recupera l'elenco dei documenti per tipo e anno.

        Per F24, naviga a Ric=VERS che mostra la tabella riepilogativa
        dei versamenti e ne estrae i link PDF.

        Per altri tipi, naviga alla servlet specifica e riutilizza i
        parser da ``cassetto_fiscale_engine.py``.
        """
        from app.cassetto_fiscale_engine import CassettoFiscaleEngine
        import requests

        ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())

        # Per F24, usa Ric=VERS
        if ric == "F24":
            return self._fetch_f24_list(anno, piva)

        # Per CUK (Certificazione Unica), naviga direttamente Ric=CUK
        if ric == "CUK":
            return self._fetch_cuk_list(anno, piva)

        # Per REDD (tutte le dichiarazioni), usa la pagina riepilogativa
        if ric == "REDD":
            return self._fetch_redd_list(anno, piva)

        # Per 730 (modello 730 precompilato)
        if ric == "730":
            return self._fetch_730_list(anno, piva)

        # Per altri tipi
        status, html_text = self.navigate_servlet(ric, anno, piva=piva)

        if status not in (200, 204):
            self.logger(
                f"  Documenti {ric}/{anno}: HTTP {status} — nessun dato."
            )
            return []

        if status == 204 or not html_text.strip():
            self.logger(f"  Documenti {ric}/{anno}: risposta vuota.")
            return []

        # Riutilizza il parser dalla engine requests-based
        dummy_engine = CassettoFiscaleEngine(
            requests.Session(), self.logger
        )
        records = dummy_engine.parse_table_from_html(html_text)

        if not records:
            docs = dummy_engine.parse_document_links_from_html(html_text)
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

        # Filtra link spuri (navigazione, menu, help) come nello stesso metodo
        # della engine requests-based (cassetto_fiscale_engine.py).
        if records:
            filtered = [
                r for r in records
                if r.get("url")
                and "Ric=HOME" not in r["url"]
                and "Ric=Help" not in r["url"]
                and "Ric=Menu" not in r["url"]
                and "Ric=IND" not in r["url"]
            ]
            scartati = len(records) - len(filtered)
            if scartati:
                self.logger(
                    f"  Filtrati {scartati} link di navigazione."
                )
            records = filtered

        return records

    def _fetch_f24_list(
        self, anno: int, piva: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Estrae la lista dei versamenti F24 dalla pagina Ric=VERS.

        La pagina contiene una tabella con:
          - Data versamento
          - Numero modelli F24
          - Saldo
          - Protocollo telematico
          - Link PDF (Ric=DetF24&indice=N&stampa=Q)
          - Link dettaglio (Ric=DetF24&indice=N)
        """
        self.logger(f"Fetch lista F24 per {piva or 'utente'} anno {anno}...")

        status, html = self.navigate_servlet("VERS", anno, piva=piva)
        if status not in (200, 204) or not html:
            self.logger(f"  VERS/{anno}: HTTP {status} o HTML vuoto")
            return []

        records = []
        page = self._page

        # Estrai righe tabella versamenti
        # La tabella ha <table class="table table-hover">
        # Le righe dati hanno: <th>data</th> <td>num</td> <td>importo</td> <td>protocollo</td> <td>link pdf</td>
        # Prima prova il parser DOM (più robusto), poi fallback regex
        js_success = False
        try:
            rows = page.query_selector_all(
                "table.table-hover > tbody > tr, "
                "table.table-hover > tr"
            )

            for row in rows:
                # Verifica che la riga abbia link stampa=Q (filtra header/footer)
                if not row.query_selector("a[href*='stampa=Q']"):
                    continue

                # Prendi tutti i children (td e th)
                cells = row.query_selector_all("th, td")
                if len(cells) < 4:
                    continue

                # Data (è in <th>)
                data = cells[0].inner_text().strip()

                # Trova protocollo scorrendo le celle
                protocollo = ""
                for c in cells[1:]:
                    h = c.get_attribute("headers") or ""
                    txt = c.inner_text().strip()
                    if "protocollo" in h.lower():
                        protocollo = txt
                        break

                # Link PDF (stampa=Q)
                pdf_link = row.query_selector("a[href*='stampa=Q']")
                pdf_url = ""
                if pdf_link:
                    href = pdf_link.get_attribute("href") or ""
                    if href.startswith("/"):
                        pdf_url = f"https://cassetto.agenziaentrate.gov.it{href}"
                    else:
                        pdf_url = href

                if data and pdf_url:
                    record = {
                        "tipo": "F24",
                        "data": data,
                        "protocollo": protocollo,
                        "url": pdf_url,
                        "descrizione": f"F24 {data} {protocollo}".strip(),
                    }
                    records.append(record)

            if records:
                js_success = True

        except Exception as e:
            self.logger(f"  Parsing DOM tabella F24 fallito: {e}")

        if not js_success:
            self.logger("  Fallback a parsing regex HTML...")
            records = self._parse_f24_table_html(html)

        self.logger(f"  F24/{anno}: trovati {len(records)} versamenti")
        return records

    def _fetch_cuk_list(
        self, anno: int, piva: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Estrae la lista delle Certificazioni Uniche (CU) per l'anno richiesto.

        Naviga prima direttamente a Ric=CUK&Anno=YYYY, poi in caso di insuccesso
        cerca nella pagina REDD (fallback).

        Per ogni CU trovata:
          1. Naviga alla pagina di dettaglio
          2. Cerca il pulsante "Genera PDF" (#tab-3) e cliccalo
          3. Cattura il download con expect_download()
          4. Fallback: costruisce URL con &stampa=P
        """
        self.logger(f"Fetch lista CU per {piva or 'utente'} anno {anno}...")

        import re
        page = self._page

        # 1. Prova navigazione diretta a Ric=CUK&Anno=YYYY
        #    (la pagina REDD spesso non contiene link diretti alle CU per l'anno)
        cu_links = []
        try:
            status, html = self.navigate_servlet("CUK", anno, piva=piva)
            if status in (200, 204) and html:
                # Estrai link con Protocollo= (dettaglio CU)
                try:
                    for a in page.query_selector_all("a[href*='Ric=CUK']"):
                        href = a.get_attribute("href") or ""
                        if "Ric=HOME" in href or "Ric=Help" in href or "Ric=Menu" in href:
                            continue
                        if "Protocollo=" in href:
                            cu_links.append(href)
                except Exception:
                    pass
                # Fallback regex
                if not cu_links:
                    for m in re.finditer(
                        r'<a[^>]*href="([^"]*Ric=CUK[^"]*Protocollo=[^"]*)"[^>]*>',
                        html
                    ):
                        h = m.group(1)
                        if h not in cu_links:
                            cu_links.append(h)

                if cu_links:
                    self.logger(f"  Trovati {len(cu_links)} link CU in Ric=CUK/{anno}")
        except Exception as e:
            self.logger(f"  Navigazione diretta CUK/{anno}: {e}")

        # 2. Fallback: naviga a REDD per cercare link CU
        if not cu_links:
            self.logger(f"  Fallback: cerco CU nella pagina REDD/{anno}...")
            try:
                status, html = self.navigate_servlet("REDD", anno, piva=piva)
                if status in (200, 204) and html:
                    # Estrai link CU
                    try:
                        for a in page.query_selector_all("a[href*='Ric=CUK']"):
                            href = a.get_attribute("href") or ""
                            if "Ric=HOME" in href or "Ric=Help" in href or "Ric=Menu" in href:
                                continue
                            if "Protocollo=" in href:
                                cu_links.append(href)
                    except Exception:
                        pass
                    if not cu_links:
                        for m in re.finditer(
                            r'<a[^>]*href="([^"]*Ric=CUK[^"]*Protocollo=[^"]*)"[^>]*>',
                            html
                        ):
                            h = m.group(1)
                            if h not in cu_links:
                                cu_links.append(h)
            except Exception as e:
                self.logger(f"  Fallback REDD/{anno}: {e}")

            # Filtra CU per anno richiesto (REDD può mostrare CU di più anni)
            if cu_links:
                requested_anno = str(anno)
                filtered = []
                for href in cu_links:
                    m = re.search(r'[?&]Anno=(\d{4})', href)
                    link_anno = m.group(1) if m else requested_anno
                    if link_anno == requested_anno:
                        filtered.append(href)
                    else:
                        self.logger(f"  Skip CU anno {link_anno} (richiesto {requested_anno})")
                cu_links = filtered
                if cu_links:
                    self.logger(f"  Trovati {len(cu_links)} link CU in REDD/{anno}")

        if not cu_links:
            self.logger(f"  Nessuna CU trovata per l'anno {anno}")
            return []

        # 3. Per ogni link CU, naviga al dettaglio e cerca il PDF
        records = []
        seen_urls = set()

        for cu_href in cu_links:
            detail_url = f"https://cassetto.agenziaentrate.gov.it{cu_href}" if cu_href.startswith("/") else cu_href

            # Estrai protocollo dall'URL
            prot_match = re.search(r'Protocollo=([^&]+)', detail_url)
            protocollo = prot_match.group(1) if prot_match else ""

            try:
                self.logger(f"  Navigo dettaglio CU: protocollo={protocollo[:30] if protocollo else '?'}...")
                page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)

                # Salva HTML per debug
                try:
                    detail_html = page.content()
                    debug_path = f"/tmp/cassetto_debug_cuk_{protocollo[:20] if protocollo else 'unknown'}.html"
                    with open(debug_path, "w", encoding="utf-8") as f:
                        f.write(detail_html)
                    self.logger(f"    HTML dettaglio salvato: {debug_path}")
                except Exception:
                    pass

                # Strategia 1: Clicca il pulsante "Genera PDF" (form POST)
                # La pagina dettaglio CU usa un form POST, non un link GET.
                # Il pulsante è nel tab #tab-3 (inizialmente nascosto).
                pdf_url = None
                local_path = None

                try:
                    # 1) Attiva il tab "Riproduci in pdf il modello"
                    tab_link = page.query_selector("a[href='#tab-3']")
                    if tab_link:
                        self.logger(f"    Attivo tab 'Riproduci in pdf il modello'...")
                        tab_link.click()
                        page.wait_for_timeout(1500)  # attesa transizione tab

                    # 2) Trova il pulsante "Genera PDF" ora visibile
                    genera_btn = page.query_selector("#tab-3 button[type='submit']")
                    if not genera_btn:
                        genera_btn = page.query_selector("button:has-text('Genera PDF')")
                    if not genera_btn:
                        genera_btn = page.query_selector("button:has-text('genera')")

                    if genera_btn:
                        self.logger(f"    Trovato pulsante 'Genera PDF', clicco...")
                        with page.expect_download(timeout=45000) as download_info:
                            genera_btn.click()
                        download = download_info.value

                        tmp_dir = os.path.join(tempfile.gettempdir(), "cuk_pdf")
                        os.makedirs(tmp_dir, exist_ok=True)
                        local_path = os.path.join(
                            tmp_dir,
                            f"CUK_{anno}_{protocollo[:20] if protocollo else 'unknown'}.pdf"
                        )
                        download.save_as(local_path)
                        file_size = os.path.getsize(local_path)
                        self.logger(f"    ✅ PDF CU scaricato: {local_path} ({file_size} byte)")

                        # Verifica magic bytes PDF
                        with open(local_path, "rb") as f:
                            is_pdf = f.read(5) == b"%PDF-"
                        if not is_pdf:
                            self.logger(f"    ⚠️ Non è un PDF valido, rimuovo")
                            os.remove(local_path)
                            local_path = None
                        else:
                            pdf_url = f"file://{local_path}"
                    else:
                        self.logger(f"    Pulsante 'Genera PDF' non trovato, provo strategie alternative...")
                except Exception as e:
                    self.logger(f"    ❌ Errore click Genera PDF: {e}")

                # Strategia 2: Cerca link con stampa=P o stampa=Q (fallback)
                if not pdf_url:
                    for selector in [
                        "a[href*='stampa=P']",
                        "a[href*='stampa=Q']",
                        "a[href*='stampa']",
                        "a[onclick*='stampa']",
                        "input[onclick*='stampa']",
                        "button[onclick*='stampa']",
                        "a[href*='download']",
                        "a[href$='.pdf']",
                        "embed[type='application/pdf']",
                        "object[type='application/pdf']",
                    ]:
                        el = page.query_selector(selector)
                        if el:
                            tag = el.evaluate("el => el.tagName").lower()
                            if tag == "embed" or tag == "object":
                                src = el.get_attribute("src") or el.get_attribute("data") or ""
                                if src:
                                    pdf_url = f"https://cassetto.agenziaentrate.gov.it{src}" if src.startswith("/") else src
                                    self.logger(f"    Trovato embed/object PDF: {pdf_url[:60]}...")
                                    break
                            else:
                                href = el.get_attribute("href") or ""
                                if href:
                                    pdf_url = f"https://cassetto.agenziaentrate.gov.it{href}" if href.startswith("/") else href
                                    self.logger(f"    Trovato link con '{selector}': {pdf_url[:60]}...")
                                    break
                                onclick = el.get_attribute("onclick") or ""
                                if onclick and "stampa" in onclick:
                                    url_match = re.search(r"['\"]([^'\"]*stampa=[PQ][^'\"]*)['\"]", onclick)
                                    if url_match:
                                        href2 = url_match.group(1)
                                        pdf_url = f"https://cassetto.agenziaentrate.gov.it{href2}" if href2.startswith("/") else href2
                                        self.logger(f"    Trovato onclick stampa: {pdf_url[:60]}...")
                                    break

                # Strategia 3: Costruisci URL con &stampa=P (fallback)
                if not pdf_url:
                    base_url = detail_url.split("&stampa=")[0]
                    if "?" in base_url:
                        stampa_url = f"{base_url}&stampa=P"
                    else:
                        stampa_url = f"{base_url}?stampa=P"
                    self.logger(f"    Provo stampa=P diretto: {stampa_url[:80]}...")
                    try:
                        resp = page.goto(stampa_url, wait_until="networkidle", timeout=20000)
                        if resp:
                            body = resp.body()
                            if body[:5] == b"%PDF-":
                                pdf_dir = os.path.join("output", "debug_cuk")
                                os.makedirs(pdf_dir, exist_ok=True)
                                fname = f"CUK_{anno_cu}_{protocollo[:20] if protocollo else 'unknown'}.pdf"
                                fpath = os.path.join(pdf_dir, fname)
                                with open(fpath, "wb") as f:
                                    f.write(body)
                                self.logger(f"    ✅ PDF salvato via stampa=P diretto: {fpath}")
                                pdf_url = stampa_url
                                local_path = fpath
                            else:
                                self.logger(f"    stampa=P diretto: risposta non PDF ({resp.headers.get('Content-Type','?')}, {len(body)} byte)")
                                page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
                                page.wait_for_timeout(500)
                    except Exception as e2:
                        self.logger(f"    stampa=P diretto fallito: {e2}")
                        try:
                            page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
                            page.wait_for_timeout(500)
                        except Exception:
                            pass

                if (pdf_url or local_path) and ((pdf_url or local_path) not in seen_urls):
                    dedup_key = local_path or pdf_url
                    seen_urls.add(dedup_key)
                    records.append({
                        "tipo": "CUK",
                        "data": str(anno),  # usa l'anno richiesto dall'utente
                        "protocollo": protocollo,
                        "url": pdf_url or "",
                        "local_path": local_path,
                        "descrizione": f"Certificazione Unica {anno} {protocollo}".strip(),
                    })
                    self.logger(f"    ✅ Trovata CU: {(pdf_url or local_path or '?')[:60]}...")
                else:
                    self.logger(f"    ⚠️ Nessun PDF trovato per protocollo={protocollo[:30] if protocollo else '?'}")

            except Exception as e:
                self.logger(f"    ❌ Errore navigazione dettaglio CU: {e}")

        self.logger(f"  CU: trovate {len(records)} Certificazioni Uniche")
        return records

    def _fetch_730_list(
        self, anno: int, piva: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Estrae il documento 730 precompilato per l'anno richiesto.

        La pagina Ric=730 mostra il modello 730 come HTML.
        Il PDF è accessibile tramite link con parametro ``stampa=P`` o ``stampa=Q``
        oppure tramite pulsante JavaScript.

        Strategie:
          1. Naviga a Ric=730&Anno=YYYY e cerca link stampa=P/Q
          2. Clicca su elementi con testo "Stampa", "Scarica", "PDF" e cattura download
        """
        self.logger(f"Fetch 730 per {piva or 'utente'} anno {anno}...")
        import re
        page = self._page
        records: List[Dict[str, Any]] = []
        seen_urls: set = set()

        # 1. Naviga a Ric=730&Anno=YYYY
        status, html = self.navigate_servlet("730", anno, piva=piva)
        if status not in (200, 204) or not html:
            self.logger(f"  730/{anno}: HTTP {status} o HTML vuoto")
            return []

        # 2. Cerca link PDF nella pagina servlet
        #    (stampa=P per inline, stampa=Q per download)
        pdf_urls: List[str] = []

        # 2a. Regex su HTML
        for m in re.finditer(
            r'<a[^>]*href="([^"]*stampa=[PQ][^"]*)"[^>]*>', html
        ):
            href = m.group(1)
            if href.startswith("/"):
                full = f"https://cassetto.agenziaentrate.gov.it{href}"
            else:
                full = href
            if full not in seen_urls:
                seen_urls.add(full)
                pdf_urls.append(full)

        # 2b. Se nessun link trovato, prova query_selector sul DOM live
        if not pdf_urls:
            try:
                for el in page.query_selector_all(
                    "a[href*='stampa=P'], a[href*='stampa=Q']"
                ):
                    href = el.get_attribute("href") or ""
                    if not href:
                        continue
                    full = href if href.startswith("http") else \
                        f"https://cassetto.agenziaentrate.gov.it{href}"
                    if full not in seen_urls:
                        seen_urls.add(full)
                        pdf_urls.append(full)
            except Exception:
                pass

        # 2c. Cerca anche bottoni/form che potrebbero generare PDF
        if not pdf_urls:
            self.logger("  Nessun link stampa trovato — cerco bottoni download...")
            try:
                # Cerca qualsiasi elemento cliccabile con testo rilevante
                for selector, text in [
                    ("a, button", "stampa"),
                    ("a, button", "scarica"),
                    ("a, button", "download"),
                    ("a, button", "PDF"),
                    ("a, button", "730"),
                ]:
                    if pdf_urls:
                        break
                    for el in page.query_selector_all(selector):
                        txt = (el.inner_text() or "").strip().lower()
                        if text in txt:
                            href = el.get_attribute("href") or ""
                            onclick = el.get_attribute("onclick") or ""
                            if href:
                                full = href if href.startswith("http") else \
                                    f"https://cassetto.agenziaentrate.gov.it{href}"
                                if full not in seen_urls:
                                    seen_urls.add(full)
                                    pdf_urls.append(full)
                                    self.logger(
                                        f"  Trovato link con testo '{txt}': {full[:60]}..."
                                    )
                            elif onclick and "window" in onclick:
                                # Potrebbe essere JavaScript — estrai URL
                                js_match = re.search(
                                    r"(?:location|open)\s*[=:]\s*['\"]([^'\"]+)['\"]",
                                    onclick,
                                )
                                if js_match:
                                    js_url = js_match.group(1)
                                    full = js_url if js_url.startswith("http") else \
                                        f"https://cassetto.agenziaentrate.gov.it{js_url}"
                                    if full not in seen_urls:
                                        seen_urls.add(full)
                                        pdf_urls.append(full)
                                        self.logger(
                                            f"  Trovato URL JS: {full[:60]}..."
                                        )
            except Exception as e:
                self.logger(f"  Ricerca bottoni fallita: {e}")

        # 3. Costruisci records
        for pdf_url in pdf_urls:
            # Estrai parametri dall'URL
            prot_match = re.search(r'Protocollo=([^&]+)', pdf_url)
            protocollo = prot_match.group(1) if prot_match else ""

            records.append({
                "tipo": "730",
                "data": str(anno),
                "protocollo": protocollo,
                "url": pdf_url,
                "descrizione": f"Modello 730 {anno} {protocollo}".strip(),
            })

        self.logger(f"  730: trovati {len(records)} documenti per l'anno {anno}.")
        return records

    def _fetch_redd_list(
        self, anno: int, piva: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Estrae tutte le dichiarazioni (CU, 730, UNI) dalla pagina REDD.
        """
        self.logger(f"Fetch lista dichiarazioni (REDD) per {piva or 'utente'} anno {anno}...")
        records = []

        # 1. Naviga a REDD
        status, html = self.navigate_servlet("REDD", anno, piva=piva)
        if status not in (200, 204) or not html:
            self.logger(f"  REDD/{anno}: HTTP {status} o HTML vuoto")
            return []

        # 2. Estrai CU (Certificazione Unica) direttamente da REDD
        # I link CU sono tipo: Ric=CUK&Anno=2024&Protocollo=T...
        from html.parser import HTMLParser
        class CUParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    href = ""
                    for attr, value in attrs:
                        if attr == 'href':
                            href = value
                            break
                    if "Ric=CUK" in href and "Protocollo=" in href:
                        self.links.append(href)
        
        cu_parser = CUParser()
        cu_parser.feed(html)
        
        for cu_href in cu_parser.links:
            # Per ogni CU, dobbiamo navigare alla pagina di dettaglio per trovare il PDF
            self.logger(f"  Analisi CU: {cu_href[:60]}...")
            try:
                # Naviga alla pagina di dettaglio CU
                detail_page_url = f"https://cassetto.agenziaentrate.gov.it{cu_href}" if cu_href.startswith("/") else cu_href
                self._page.goto(detail_page_url, wait_until="domcontentloaded", timeout=20000)
                self._page.wait_for_timeout(1000)
                
                # Cerca link PDF (stampa=P o stampa=Q)
                pdf_link_el = self._page.query_selector("a[href*='stampa=P'], a[href*='stampa=Q']")
                if pdf_link_el:
                    href = pdf_link_el.get_attribute("href") or ""
                    if href.startswith("/"):
                        pdf_url = f"https://cassetto.agenziaentrate.gov.it{href}"
                    else:
                        pdf_url = href
                    
                    # Estrai protocollo dall'URL (se presente)
                    import re
                    prot_match = re.search(r'Protocollo=([^&]+)', pdf_url)
                    protocollo = prot_match.group(1) if prot_match else ""
                    
                    # Se non c'è protocollo nell'URL, prova a prenderlo dalla pagina
                    if not protocollo:
                        # Cerca nel testo della pagina o in altri elementi
                        protocollo = "" # fallback
                    
                    records.append({
                        "tipo": "CUK",
                        "data": str(anno),
                        "protocollo": protocollo,
                        "url": pdf_url,
                        "descrizione": f"Certificazione Unica {anno} {protocollo}".strip(),
                    })
                    self.logger(f"    ✅ Trovato PDF: {pdf_url[:60]}...")
                else:
                    self.logger(f"    ⚠️ Nessun link PDF trovato nella pagina dettaglio CU")
            except Exception as e:
                self.logger(f"    ❌ Errore durante analisi CU: {e}")


        # 3. Naviga a 730 e UNI per trovare altri documenti
        for ric in ["730", "UNI"]:
            self.logger(f"  Navigo a {ric} per trovare altri documenti...")
            try:
                s, h = self.navigate_servlet(ric, anno, piva=piva)
                if s == 200 and h:
                    # Qui usiamo il parser della engine per estrarre i link PDF
                    from app.cassetto_fiscale_engine import CassettoFiscaleEngine
                    import requests
                    dummy_engine = CassettoFiscaleEngine(requests.Session(), self.logger)
                    
                    # Estrai link PDF (stampa=P o stampa=Q)
                    # Usiamo il metodo esistente se possibile, o parsing manuale
                    # Per semplicità, usiamo il parsing manuale per i link PDF
                    import re
                    pdf_pattern = re.compile(r'<a[^>]*href="([^"]*stampa=[PQ][^"]*)"[^>]*>')
                    for match in pdf_pattern.finditer(h):
                        href = match.group(1)
                        if href.startswith("/"):
                            pdf_url = f"https://cassetto.agenziaentrate.gov.it{href}"
                        else:
                            pdf_url = href
                        
                        # Estrai protocollo se presente nell'URL
                        prot_match = re.search(r'Protocollo=([^&]+)', pdf_url)
                        protocollo = prot_match.group(1) if prot_match else ""
                        
                        records.append({
                            "tipo": ric,
                            "data": str(anno),
                            "protocollo": protocollo,
                            "url": pdf_url,
                            "descrizione": f"{ric} {anno} {protocollo}".strip(),
                        })
            except Exception as e:
                self.logger(f"  Errore navigazione {ric}: {e}")

        self.logger(f"  Dichiarazioni {anno}: trovati {len(records)} elementi.")
        return records

    # ── Download document ────────────────────────────────────────────────────

    def download_document(
        self,
        url: str,
        output_dir: str,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Scarica un documento (PDF/P7M) usando il contesto del browser.

        Per URL che triggerano download (es. ``stampa=Q``), usa l'API
        Playwright ``expect_download()`` con ``window.location``.
        Fallback: requests con i cookie della sessione browser.
        """
        import requests as req_module
        from app.security import get_ca_bundle

        # Determina estensione
        ext = ".bin"
        url_lower = url.lower()
        if ".pdf" in url_lower:
            ext = ".pdf"
        elif ".p7m" in url_lower:
            ext = ".p7m"
        elif "stampa=q" in url_lower or "stampa=Q" in url_lower:
            ext = ".pdf"

        # Nome file
        if not filename:
            filename = url.rstrip("/").split("/")[-1].split("?")[0]
            if not filename:
                filename = f"documento_{int(time.time() * 1000)}"
            if not filename.lower().endswith(ext):
                filename += ext

        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)

        # Prova download via browser (se page disponibile)
        if self._page and not self._page.is_closed():
            # Prova prima: expect_download per URL che triggerano download
            try:
                self.logger(f"Download via browser (expect_download): {url}")

                # Per URL che triggerano download (Content-Disposition), usa
                # expect_download + window.location per evitare l'errore
                # "Download is starting" da page.goto()
                with self._page.expect_download(timeout=15000) as download_info:
                    self._page.evaluate(f"window.location.href = '{url}'")

                download = download_info.value
                self.logger(
                    f"  Download catturato: {download.suggested_filename} "
                    f"({download.url})"
                )

                # Usa il filename suggerito se filename non specificato
                if not filename or filename == url.rstrip("/").split("/")[-1].split("?")[0]:
                    suggested = download.suggested_filename
                    if suggested:
                        ext2 = os.path.splitext(suggested)[1] or ext
                        fname_base = os.path.splitext(filename)[0] if filename else ""
                        if ext2 and not filename:
                            filename = suggested
                        elif ext2 and not filename.lower().endswith(ext2):
                            filename = f"{fname_base}{ext2}"
                        filepath = os.path.join(output_dir, filename)

                download.save_as(filepath)
                file_size = os.path.getsize(filepath)

                # Verifica magic bytes
                is_pdf = False
                if file_size > 4:
                    with open(filepath, "rb") as f:
                        is_pdf = f.read(5) == b"%PDF-"

                self.logger(
                    f"  Salvato: {filepath} ({file_size} byte, "
                    f"PDF={is_pdf})"
                )
                return filepath

            except Exception as e:
                self.logger(f"  Download via expect_download fallito: {e}")

            # Prova seconda: page.goto() + response.body() per URL che
            # restituiscono PDF inline (es. stampa=P) senza triggerare download
            try:
                self.logger(f"Download via browser (page.goto): {url}")
                resp = self._page.goto(url, wait_until="networkidle", timeout=30000)
                if resp:
                    body = resp.body()
                    if body[:5] == b"%PDF-":
                        with open(filepath, "wb") as f:
                            f.write(body)
                        file_size = os.path.getsize(filepath)
                        self.logger(
                            f"  Salvato: {filepath} ({file_size} byte, "
                            f"PDF=True)"
                        )
                        return filepath
                    else:
                        self.logger(
                            f"  page.goto: risposta non PDF "
                            f"(Content-Type={resp.headers.get('Content-Type','?')}, "
                            f"size={len(body)})"
                        )
                else:
                    self.logger("  page.goto: nessuna risposta")
            except Exception as e:
                self.logger(f"  Download via page.goto fallito: {e}")

        # Fallback: usa requests con i cookie dalla sessione browser
        self.logger("Download via requests (fallback)...")
        try:
            # Estrai cookie dal contesto browser
            cookies = {}
            if self._context:
                for c in self._context.cookies():
                    cookies[c["name"]] = c["value"]

            session = req_module.Session()
            for name, value in cookies.items():
                session.cookies.set(name, value)

            # Gestione URL relativo
            full_url = url
            if url.startswith("/"):
                full_url = f"https://cassetto.agenziaentrate.gov.it{url}"

            r = session.get(
                full_url,
                headers={
                    "Referer": CASSETTO_SERVLET,
                    "Accept": "application/pdf, */*",
                },
                verify=get_ca_bundle(),
                timeout=60,
            )
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                is_pdf_magic = r.content[:5] == b"%PDF-"
                self.logger(
                    f"  Download requests: HTTP 200, "
                    f"Content-Type={ct}, size={len(r.content)}, "
                    f"PDF magic={is_pdf_magic}"
                )
                with open(filepath, "wb") as f:
                    f.write(r.content)
                self.logger(f"  Salvato: {filepath}")
                return filepath
            else:
                self.logger(f"  Download requests: HTTP {r.status_code}")
        except Exception as e:
            self.logger(f"  Download requests fallito: {e}")

        return None

    # ── Chiusura ─────────────────────────────────────────────────────────────

    def close(self):
        """Chiude il browser e rilascia le risorse."""
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
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._initialized = False
        self.logger("Browser chiuso.")


# ═══════════════════════════════════════════════════════════════════════════════
# Factory / funzione di comodo
# ═══════════════════════════════════════════════════════════════════════════════


def run_with_browser(
    cf: str,
    pin: str,
    password: str,
    piva: str,
    anno: int,
    tipo: str,
    logger: Callable[..., None],
    output_root: str = "output",
    download_pdf: bool = True,
) -> bool:
    """
    Esegue download dichiarazioni/F24 usando il browser engine.

    Args:
        cf: CF utente.
        pin: PIN.
        password: Password.
        piva: P.IVA cliente.
        anno: Anno.
        tipo: Tipo documento ("F24", "RED", "UNI", etc.).
        logger: Logger.
        output_root: Directory output.
        download_pdf: Se True, scarica PDF.

    Returns:
        True se completato.
    """
    engine = CassettoFiscaleBrowserEngine(logger, headless=True)
    try:
        engine.init_session(cf, pin, password)

        ric = TIPO_TO_RIC.get(tipo.lower().strip(), tipo.upper().strip())
        tipo_label = TIPI_DOCUMENTO.get(ric, ric)
        logger(f"\n=== Download {tipo_label} per {piva} anno {anno} ===")

        records = engine.fetch_document_list(tipo, anno, piva=piva)
        if not records:
            logger(f"Nessun documento trovato per {piva} anno {anno}")
            return True

        # Directory output: output/<PIVA>/<ANNO>/f24generici/ o certificazioniuniche/
        if ric in ("F24", "DetF24"):
            categ_folder = "f24generici"
        elif ric == "CUK":
            categ_folder = "certificazioniuniche"
        else:
            categ_folder = f"dichiarazioni_{ric.lower()}"
        base_output = os.path.join(output_root, piva, str(anno), categ_folder)

        json_path = export_json(records, piva, anno, categ, base_output)
        logger(f"Metadati salvati: {json_path}")

        if download_pdf:
            pdf_dir = os.path.join(base_output, "PDF")
            scaricati = 0
            for idx, doc in enumerate(records, 1):
                url = doc.get("url") or doc.get("href") or ""
                if not url:
                    continue
                desc = doc.get("descrizione") or doc.get("oggetto") or ""
                data = doc.get("data") or doc.get("Data") or str(anno)
                protocollo = doc.get("protocollo", "")
                if protocollo:
                    prot_clean = protocollo.replace("/", "_").replace("\\", "_")
                    fname = f"{tipo_label}_{data.replace('/', '-')}_{prot_clean}.pdf"
                else:
                    fname = f"{tipo_label}_{data.replace('/', '-')}_{idx:02d}.pdf"
                fname = re.sub(r'[\\/*?:"<>|]', "_", fname)
                fpath = engine.download_document(url, pdf_dir, filename=fname)
                if fpath:
                    scaricati += 1

            logger(f"Scaricati {scaricati}/{len(records)} documenti")

        return True

    finally:
        engine.close()


__all__ = [
    "CassettoFiscaleBrowserEngine",
    "run_with_browser",
    "PLAYWRIGHT_AVAILABLE",
]
