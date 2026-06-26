#!/usr/bin/env python3
"""Test dichiarazioni (opzione 6) per CF RZZFNC06B02L628M anno 2024 tipo 730/UNI."""
import logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_dich")

from app.config import config
config.load()

CF_UTENTE = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

# Cliente da testare
CF_CLIENTE = "RZZFNC06B02L628M"
ANNO = 2024

# Test 1: Ric=REDD
from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

log.info("=" * 60)
log.info("TEST 1: Ric=REDD per il cliente (con cambio cliente se possibile)")
log.info("=" * 60)

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    engine.init_session(CF_UTENTE, PIN, PASSWORD)
    
    # Prova REDD/730/UNI SENZA cambio cliente (usa parametri servlet diretti)
    # Per CF che non sono nel dropdown PortaleWeb, usiamo extra_params
    for ric in ["REDD", "RED", "730", "UNI"]:
        log.info(f"\n--- Navigo Ric={ric} Anno={ANNO} (senza cambio cliente) ---")
        try:
            # Non passa piva per evitare cambio_cliente; usa cf come parametro extra
            status, html = engine.navigate_servlet(
                ric, ANNO, piva=None,
                extra_params={"cf": CF_CLIENTE, "pIva": CF_CLIENTE}
            )
            log.info(f"  Status={status}, HTML size={len(html)}")
            
            # Salva per analisi
            fname = f"/tmp/dich_{ric}_{ANNO}.html"
            with open(fname, "w") as f:
                f.write(html)
            
            # Testo per capire
            from html.parser import HTMLParser
            class T(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.t = []
                    self._script = False
                def handle_starttag(self, tag, attrs):
                    if tag == 'script': self._script = True
                def handle_endtag(self, tag):
                    if tag == 'script': self._script = False
                def handle_data(self, d):
                    if not self._script and d.strip():
                        self.t.append(d.strip())
            parser = T()
            parser.feed(html)
            log.info(f"  Testo: {' | '.join(parser.t[:15])[:300]}")
            
            # Cerca documenti
            has_docs = "Documenti presenti" in html
            has_error = "Mancata autenticazione" in html or "errore" in html.lower()[:500]
            log.info(f"  Documenti presenti={has_docs}, errore={has_error}")
            
        except Exception as e:
            log.info(f"  ❌ {e}")

finally:
    engine.close()
