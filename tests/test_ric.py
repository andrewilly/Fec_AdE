#!/usr/bin/env python3
"""Prova diversi Ric per trovare la lista F24 corretta."""
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_ric")

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

    # Prova diversi Ric navigando direttamente (non via navigate_servlet per evitare cambio cliente)
    CASSETTO_SERVLET = "https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet"
    from urllib.parse import urlencode
    from playwright.sync_api import sync_playwright

    test_urls = [
        f"{CASSETTO_SERVLET}?Ric=F24&Anno=2026",
        f"{CASSETTO_SERVLET}?Ric=F24&Anno=2026&tipo=F24",
        f"{CASSETTO_SERVLET}?Ric=VERS&Anno=2026",
        f"{CASSETTO_SERVLET}?Ric=VERS",
        f"{CASSETTO_SERVLET}?Ric=PAG&Anno=2026",
        f"{CASSETTO_SERVLET}?Ric=DetF24&Anno=2026",
        # Prova con CF dell'utente
        f"{CASSETTO_SERVLET}?Ric=F24&Anno=2026&cf={CF}",
    ]
    
    for url in test_urls:
        log.info(f"\n--- GET {url} ---")
        try:
            resp = be._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            be._page.wait_for_timeout(2000)
            html = be._page.content()
            log.info(f"  size={len(html)}")
            
            from html.parser import HTMLParser
            class T(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.t = []
                def handle_data(self, d):
                    if d.strip():
                        self.t.append(d.strip())
            parser = T()
            parser.feed(html)
            text = ' | '.join(parser.t[:20])[:500]
            log.info(f"  testi: {text}")
            
            if len(html) > 8000:
                ric = url.split("Ric=")[1].split("&")[0] if "Ric=" in url else "unknown"
                fname = f"/tmp/servlet_{ric}_2026.html"
                with open(fname, "w") as f:
                    f.write(html)
                log.info(f"  ✅ Salvato in {fname}")
            else:
                log.info(f"  ⚠️ HTML piccolo (probabile errore)")
        except Exception as e:
            log.info(f"  ❌ {e}")

finally:
    be.close()
