#!/usr/bin/env python3
"""Scopri come funziona Cambia utenza su PortaleWeb/cambiautenza."""
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_cambiautenza")

from app.config import config
config.load()
CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    
    # 1. Login SSO
    log.info("Passo 1: Login SSO su iampe...")
    page.goto("https://iampe.agenziaentrate.gov.it/samlsso/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    log.info(f"  Pagina: {page.title()}")
    
    # Clicca tab Fisconline/Entratel
    try:
        page.click("button[aria-label='Fisconline/Entratel']", timeout=5000)
        log.info("  Tab Fisconline/Entratel cliccato")
    except:
        try:
            page.click("button:has-text('Fisconline')", timeout=5000)
            log.info("  Tab Fisconline cliccato (fallback)")
        except:
            log.info("  Tab già selezionato?")
    
    page.wait_for_timeout(1000)
    
    # Compila form
    page.fill("input[type='text']:visible", PIN)
    page.fill("input[type='password']:visible", PASSWORD)
    page.wait_for_timeout(500)
    
    # Clicca Accedi
    page.click("button:has-text('Accedi')")
    log.info("  Click Accedi...")
    
    page.wait_for_timeout(5000)
    log.info(f"  Redirect a: {page.url}")
    
    page.wait_for_url("**/PortaleWeb/**", timeout=15000)
    log.info(f"  Arrivato a: {page.url}")
    
    # 2. Vai al Cassetto Fiscale
    log.info("\nPasso 2: Vai al Cassetto Fiscale...")
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME", 
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    log.info(f"  Titolo: {page.title()}")
    
    # InitLight + InitCassetto
    log.info("  InitLight...")
    resp = page.evaluate("""
        () => fetch('https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=InitLight', {
            method: 'POST',
            credentials: 'include'
        }).then(r => ({status: r.status, text: r.text()}))
    """)
    log.info(f"    InitLight: HTTP {resp['status']}")
    
    log.info("  InitCassetto...")
    resp = page.evaluate("""
        () => fetch('https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=InitCassetto', {
            method: 'POST',
            credentials: 'include'
        }).then(r => ({status: r.status, text: r.text()}))
    """)
    log.info(f"    InitCassetto: HTTP {resp['status']}")
    
    # 3. Prova Cambia utenza via PortaleWeb
    log.info("\nPasso 3: Vai a PortaleWeb/cambiautenza...")
    page.goto("https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3000)
    log.info(f"  URL: {page.url}")
    log.info(f"  Titolo: {page.title()}")
    
    # Screenshot per vedere la pagina
    page.screenshot(path="/tmp/cambiautenza.png")
    log.info("  Screenshot salvato in /tmp/cambiautenza.png")
    
    # Stampa il contenuto testuale
    text = page.inner_text("body")
    log.info(f"  Testo pagina:\n{text[:2000]}")
    
    # Cerca elementi interattivi
    log.info("\n  Cerca link/pulsanti...")
    links = page.query_selector_all("a")
    for l in links:
        href = l.get_attribute("href") or "(no href)"
        txt = l.inner_text().strip()
        if txt:
            log.info(f"    LINK: '{txt}' -> {href[:100]}")
    
    buttons = page.query_selector_all("button")
    for b in buttons:
        txt = b.inner_text().strip()
        if txt:
            log.info(f"    BUTTON: '{txt}'")
    
    inputs = page.query_selector_all("input[type='text'], input[type='search'], select")
    for inp in inputs:
        name = inp.get_attribute("name") or "(no name)"
        placeholder = inp.get_attribute("placeholder") or ""
        log.info(f"    INPUT: name={name}, placeholder='{placeholder}'")
    
    # 4. Prova a cercare il cliente
    log.info("\nPasso 4: Prova a selezionare cliente 17836421002...")
    
    # Prova a inserire PIVA in un campo di ricerca
    piva_input = page.query_selector("input[type='text'], input[type='search']")
    if piva_input:
        piva_input.fill("17836421002")
        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        log.info(f"  Dopo ricerca: URL={page.url}")
        page.screenshot(path="/tmp/cambiautenza_dopo_ricerca.png")
        
        text2 = page.inner_text("body")
        log.info(f"  Testo:\n{text2[:1500]}")
    
    # Se c'è un dropdown/a scelta, prova a selezionare
    selects = page.query_selector_all("select")
    for s in selects:
        options = s.query_selector_all("option")
        for opt in options:
            val = opt.get_attribute("value") or ""
            txt = opt.inner_text().strip()
            log.info(f"    SELECT OPTION: value='{val}' text='{txt}'")
    
    browser.close()
