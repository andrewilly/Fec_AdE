#!/usr/bin/env python3
"""Test cambio utenza via PortaleWeb con selezione cliente e conferma."""
import logging
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_cambia")

from app.config import config
config.load()
CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

PIVA_CLIENTE = "17836421002"

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    # Step 1: Login SSO + Cassetto init
    engine.init_session(CF, PIN, PASSWORD)
    page = engine._page
    
    # Step 2: Vai a cambiautenza
    log.info("\n=== Vado a PortaleWeb/cambiautenza ===")
    page.goto("https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
              wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1000)
    log.info(f"  Titolo: {page.title()}")
    log.info(f"  URL: {page.url}")
    
    # Step 3: Seleziona radio "Incaricato" 
    log.info("\n=== Seleziona ruolo ===")
    radio_btns = page.query_selector_all("input[name='ruoloType']")
    log.info(f"  Trovati {len(radio_btns)} radio button")
    for i, rb in enumerate(radio_btns):
        rid = rb.get_attribute('id')
        label = page.evaluate(f"document.querySelector('label[for=\"{rid}\"]')?.innerText")
        log.info(f"    Radio {i}: id={rid} label='{label}'")
    
    # Determina quale radio cliccare in base alla label
    # "Incaricato" è UTENZA=3 nel nostro sistema
    for rb in radio_btns:
        rid = rb.get_attribute('id')
        label = page.evaluate(f"document.querySelector('label[for=\"{rid}\"]')?.innerText")
        if label and "incaricato" in label.lower():
            rb.click()
            log.info(f"  Click su radio 'Incaricato'")
            page.wait_for_timeout(500)
            break
    else:
        # Fallback: clicca il primo
        if radio_btns:
            radio_btns[0].click()
            log.info("  Click su radio 0 (default)")
            page.wait_for_timeout(500)
    
    # Step 4: Seleziona PIVA cliente nel dropdown
    log.info(f"\n=== Seleziona cliente {PIVA_CLIENTE} ===")
    select = page.query_selector("select[name='utenza']")
    if select:
        options = select.query_selector_all("option")
        log.info(f"  Trovate {len(options)} option nel select")
        
        # Mostra qualche opzione per capire la struttura
        for opt in options[:5]:
            val = opt.get_attribute("value") or ""
            txt = opt.inner_text().strip()
            log.info(f"    option: value='{val}' text='{txt}'")
        
        # Cerca l'opzione con la PIVA
        target_option = None
        for opt in options:
            val = opt.get_attribute("value") or ""
            txt = opt.inner_text().strip()
            if PIVA_CLIENTE in val or PIVA_CLIENTE in txt:
                target_option = opt
                log.info(f"  ✅ Trovato: value='{val}' text='{txt}'")
                break
        
        if target_option:
            # Seleziona usando select_option con il value
            val = target_option.get_attribute("value")
            page.select_option("select[name='utenza']", val)
            page.wait_for_timeout(500)
            log.info(f"  Selezionato valore: {val}")
            
            # Verifica selezione
            selected = page.evaluate("document.querySelector('select[name=\"utenza\"]').value")
            log.info(f"  Valore selezionato (JS): {selected}")
    else:
        log.info("  Select non trovato!")
        # Esplora struttura pagina
        page.screenshot(path="/tmp/cambia_noselect.png")
        html = page.content()
        with open("/tmp/cambia_noselect.html", "w") as f:
            f.write(html)
        log.info("  HTML salvato")
        raise Exception("Select non trovato")
    
    # Step 5: Clicca Conferma
    log.info("\n=== Click Conferma ===")
    confirm_btn = page.query_selector("button:has-text('Conferma'), input[type='submit']")
    if confirm_btn:
        confirm_btn.click()
        log.info("  Click Conferma")
        page.wait_for_timeout(3000)
        log.info(f"  Dopo conferma: URL={page.url}")
        log.info(f"  Titolo: {page.title()}")
        
        # Step 6: Dopo conferma — naviga al Cassetto via PortaleWeb home
        log.info("\n=== Dopo cambio utenza, naviga al Cassetto Fiscale ===")
        
        # Siamo su PortaleWeb/home — da qui clicca su "Servizi" o naviga direttamente
        log.info("  Menu disponibile:")
        menu_items = page.query_selector_all("nav a, .menu a, .navbar a, a:has-text('Cassetto'), a:has-text('Servizi')")
        for item in menu_items:
            href = item.get_attribute("href") or ""
            txt = item.inner_text().strip()
            if txt:
                log.info(f"    '{txt}' -> {href}")
        
        # Prova a navigare direttamente a Cassetto Fiscale
        log.info("  Vado direttamente a CassettoFiscaleServlet?Ric=HOME...")
        page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        log.info(f"  URL: {page.url}")
        log.info(f"  Titolo: {page.title()}")
        
        text = page.inner_text("body")
        log.info(f"  Contenuto:\n{text[:1000]}")
        
        # Cerca il pulsante/collegamento "Indietro" o "Ripristina" o "Home"
        if "Necessario ripristino" in text or "riprist" in text.lower():
            log.info("  ⚠️ Pagina di ripristino — cerco pulsante 'Indietro' o link")
            # Prova a cliccare "Indietro" o navigare a Ric=HOME
            indietro = page.query_selector("a:has-text('Indietro'), button:has-text('Indietro')")
            if indietro:
                indietro.click()
                page.wait_for_timeout(3000)
                log.info(f"  Dopo click Indietro: {page.url}")
            
            # Oppure prova a navigare direttamente alla home
            log.info("  Navigo a Ric=HOME...")
            page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            log.info(f"  URL: {page.url}")
            log.info(f"  Titolo: {page.title()}")
            text2 = page.inner_text("body")
            log.info(f"  Contenuto:\n{text2[:1500]}")
            
            # Prova anche a cliccare "Torna" o "Home"
            home_link = page.query_selector("a:has-text('Home')")
            if home_link:
                log.info("  Click su Home...")
                home_link.click()
                page.wait_for_timeout(3000)
                log.info(f"  Dopo Home click: {page.url}")
                text3 = page.inner_text("body")
                log.info(f"  Contenuto: {text3[:1000]}")
        
        # Step 7: Prova VERS/2026
        log.info("\n  Vado a VERS/2026...")
        page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=VERS&Anno=2026",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        text4 = page.inner_text("body")
        log.info(f"  Contenuto:\n{text4[:2000]}")
        
        if "17836421002" in text4:
            log.info("  ✅ Cliente 17836421002 presente nella pagina!")
        if "LEONI" in text4:
            log.info("  ⚠️ Ancora 'LEONI' (intermediario)")
        if "Documenti presenti" in text4:
            log.info("  ✅ Documenti presenti!")
        if "Documenti non presenti" in text4:
            log.info("  📄 Documenti non presenti per 2026")
        
        page.screenshot(path="/tmp/dopo_cambio_cliente.png")
        html = page.content()
        with open("/tmp/dopo_cambio_cliente.html", "w") as f:
            f.write(html)
        log.info("  HTML salvato")
    else:
        log.info("  Pulsante Conferma non trovato!")
        page.screenshot(path="/tmp/nessun_conferma.png")

finally:
    engine.close()
