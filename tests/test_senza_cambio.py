#!/usr/bin/env python3
"""
Test: naviga F24 servlet SENZA cambio cliente (usa la sessione utente corrente).
Se l'utente T8979981 è lo stesso di PIVA 17836421002, i suoi F24 dovrebbero apparire.
"""
import logging, sys, time
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_senza_cambio")

from app.config import config
config.load()
CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from playwright.sync_api import sync_playwright
from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

be = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    be.init_session(CF, PIN, PASSWORD)
    
    # Dopo init_session, NON chiamiamo cambia_cliente
    # Ma navighiamo direttamente alla servlet F24/2026 (senza piva)
    log.info("=" * 60)
    log.info("Navigo servlet F24/2026 SENZA cambio cliente")
    log.info("=" * 60)
    
    status, html = be.navigate_servlet("F24", 2026, piva=None)
    log.info(f"Status: {status}, HTML size: {len(html)}")
    
    # Salva HTML
    with open("/tmp/f24_no_cambio.html", "w") as f:
        f.write(html)
    
    # Analizza HTML
    if "Nessun documento" in html:
        log.info("Nessun documento trovato (nessun F24 per l'utente corrente)")
    if "F24" in html:
        log.info("'F24' trovato nell'HTML!")
    if "Mancata autenticazione" in html:
        log.info("❌ Mancata autenticazione!")
    
    # Stampa testo body
    from html.parser import HTMLParser
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
            self._skip = False
        def handle_data(self, data):
            if data.strip():
                self.text.append(data.strip())
    parser = TextExtractor()
    parser.feed(html)
    body_text = " | ".join(parser.text[:50])
    log.info(f"Testo estratto: {body_text[:1000]}")
    
finally:
    be.close()
