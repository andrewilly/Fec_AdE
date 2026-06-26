#!/usr/bin/env python3
"""Test dichiarazioni: REDD -> CU, 730, UNI."""
import logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_redd_full")

from app.config import config
config.load()

CF_UTENTE = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    engine.init_session(CF_UTENTE, PIN, PASSWORD)
    
    # Test REDD
    log.info("=== Test REDD (tutte le dichiarazioni) ===")
    records = engine.fetch_document_list("REDD", 2024)
    
    log.info(f"Trovati {len(records)} documenti totali")
    for r in records:
        log.info(f"  [{r.get('tipo')}] {r.get('data')} - {r.get('descrizione')} -> {r.get('url')[:80]}")

    if records:
        log.info("\n=== Test Download primo documento ===")
        doc = records[0]
        log.info(f"Provando download: {doc.get('descrizione')}")
        path = engine.download_document(doc.get("url"), "/tmp", filename=f"test_{doc.get('tipo')}.pdf")
        if path:
            log.info(f"✅ Salvato: {path}")
        else:
            log.info("❌ Download fallito")

finally:
    engine.close()
