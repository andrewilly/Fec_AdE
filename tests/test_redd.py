#!/usr/bin/env python3
"""Analisi dettagliata della pagina Ric=REDD."""
import logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_redd")

from app.config import config
config.load()

CF_UTENTE = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    engine.init_session(CF_UTENTE, PIN, PASSWORD)
    page = engine._page
    
    # Naviga a REDD anno 2024
    log.info("=== Ric=REDD Anno=2024 (per l'utente connesso) ===")
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=REDD&Anno=2024",
              wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    
    text = page.inner_text("body")
    log.info(f"  Body:\n{text[:2500]}")
    
    html = page.content()
    with open("/tmp/redd_2024.html", "w") as f:
        f.write(html)
    log.info(f"  HTML salvato ({len(html)} byte)")
    
    # Cerca link PDF / download
    log.info("\n=== Link nella pagina ===")
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        txt = a.inner_text().strip()
        if txt and ("pdf" in href.lower() or "stampa" in href.lower() or "download" in href.lower() or "Ric=" in href):
            log.info(f"  '{txt[:50]}' -> {href[:120]}")
    
    # Cerca sezioni documento
    log.info("\n=== Sezioni documento ===")
    for h2 in page.query_selector_all("h2, h3, h4, h5, strong"):
        txt = h2.inner_text().strip()
        tag = h2.evaluate("el.tagName.toLowerCase()")
        if txt and len(txt) > 3:
            log.info(f"  <{tag}>: {txt[:80]}")

finally:
    engine.close()
