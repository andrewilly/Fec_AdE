#!/usr/bin/env python3
"""Test Cambia utenza via PortaleWeb dopo aver fatto login con CassettoFiscaleBrowserEngine."""
import logging
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_portale")

from app.config import config
config.load()
CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    # Step 1: Login SSO + init Cassetto (usando l'engine già funzionante)
    engine.init_session(CF, PIN, PASSWORD)
    page = engine._page
    
    # Step 2: Vai a PortaleWeb/cambiautenza
    log.info("\n=== Passo 2: Navigo a PortaleWeb/cambiautenza ===")
    page.goto("https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
              wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    log.info(f"  URL: {page.url}")
    log.info(f"  Titolo: {page.title()}")
    page.screenshot(path="/tmp/cambiautenza1.png")
    
    # Stampa contenuto principale
    text = page.inner_text("body")
    log.info(f"  Testo body:\n{text[:2000]}")
    
    # Trova elementi di input
    log.info("\n  Elementi interattivi:")
    for selector in ["a", "button", "input", "select", "li a"]:
        els = page.query_selector_all(selector)
        for el in els:
            try:
                txt = el.inner_text().strip()
                href = el.get_attribute("href") or ""
                name = el.get_attribute("name") or ""
                type_ = el.get_attribute("type") or ""
                if txt or href or name:
                    log.info(f"    {selector}: txt='{txt[:80]}' href='{href[:80]}' name='{name}' type='{type_}'")
            except:
                pass
    
    # Step 3: Cerca PIVA 17836421002
    log.info("\n=== Passo 3: Cerca cliente 17836421002 ===")
    
    # Cerca campo input
    search_input = page.query_selector("input[type='text'], input[type='search'], input:not([type='hidden']):not([type='password'])")
    if search_input:
        name = search_input.get_attribute("name") or ""
        placeholder = search_input.get_attribute("placeholder") or ""
        log.info(f"  Trovato input: name='{name}' placeholder='{placeholder}'")
        
        search_input.fill("17836421002")
        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        
        log.info(f"  Dopo ricerca: URL={page.url}")
        page.screenshot(path="/tmp/cambiautenza2.png")
        text2 = page.inner_text("body")
        log.info(f"  Testo:\n{text2[:1500]}")
    
    # Step 4: Prova anche a navigare direttamente a cambiautenza con parametri
    log.info("\n=== Passo 4: Prova URL alternativi ===")
    from urllib.parse import urlencode
    
    # Prova API di ricerca clienti
    ricerca_urls = [
        "https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza/ricerca",
        "https://portale.agenziaentrate.gov.it/PortaleWeb/api/cambiautenza/ricerca",
        "https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza?pIva=17836421002",
    ]
    for url in ricerca_urls:
        try:
            log.info(f"  GET {url}")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            log.info(f"    URL={page.url} size={len(page.content())}")
            if len(page.content()) > 500:
                log.info(f"    Testo: {page.inner_text('body')[:300]}")
        except Exception as e:
            log.info(f"    Errore: {e}")
    
    # Step 5: Torna al Cassetto e verifica se il cliente è cambiato
    log.info("\n=== Passo 5: Torna al Cassetto Fiscale ===")
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=VERS&Anno=2026",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    text5 = page.inner_text("body")
    log.info(f"  Testo:\n{text5[:1000]}")

finally:
    engine.close()
