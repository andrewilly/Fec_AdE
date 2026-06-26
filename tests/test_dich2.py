#!/usr/bin/env python3
"""Test dichiarazioni: verifica se RZZFNC06B02L628M è nel dropdown e testa Ric=REDD."""
import logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_dich2")

from app.config import config
config.load()

CF_UTENTE = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")
CF_CLIENTE = "RZZFNC06B02L628M"
ANNO = 2024

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    engine.init_session(CF_UTENTE, PIN, PASSWORD)
    page = engine._page
    
    # Step 1: Vedi se RZZFNC06B02L628M è nel dropdown PortaleWeb
    log.info("=== Verifico dropdown PortaleWeb ===")
    page.goto("https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
              wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1000)
    
    select = page.query_selector("select[name='utenza']")
    options = select.query_selector_all("option") if select else []
    
    # Cerca il CF cliente
    found = False
    for opt in options:
        val = opt.get_attribute("value") or ""
        txt = opt.inner_text().strip()
        if CF_CLIENTE in val or CF_CLIENTE in txt:
            log.info(f"  ✅ Trovato: value='{val}' text='{txt}'")
            found = True
    
    if not found:
        log.info(f"  ❌ {CF_CLIENTE} NON trovato nel dropdown")
        log.info("  Mostro alcune opzioni del dropdown per contesto:")
        for opt in options[:5]:
            log.info(f"    value='{opt.get_attribute('value')}' text='{opt.inner_text().strip()}'")
        log.info("  ...")
        for opt in options[-5:]:
            log.info(f"    value='{opt.get_attribute('value')}' text='{opt.inner_text().strip()}'")
    
    # Step 2: Se trovato, prova cambio cliente e REDD
    if found:
        log.info(f"\n=== Cambio cliente a {CF_CLIENTE} e test Ric=REDD ===")
        
        # Seleziona radio Incaricato
        for rb in page.query_selector_all("input[name='ruoloType']"):
            rid = rb.get_attribute("id")
            label = page.evaluate(f'document.querySelector(\'label[for="{rid}"]\')?.innerText')
            if label and "incaricato" in label.lower():
                if not rb.is_checked():
                    rb.click()
                    page.wait_for_timeout(500)
                break
        
        # Trova il valore esatto nel dropdown
        target_val = None
        for opt in options:
            val = opt.get_attribute("value") or ""
            txt = opt.inner_text().strip()
            if CF_CLIENTE in val:
                target_val = val
                break
            if CF_CLIENTE in txt:
                target_val = val
                break
        
        if target_val:
            log.info(f"  Seleziono valore: {target_val}")
            page.select_option("select[name='utenza']", target_val)
            page.wait_for_timeout(500)
            
            # Conferma
            page.click("button:has-text('Conferma')")
            page.wait_for_timeout(3000)
            log.info(f"  Confermato: {page.url}")
            
            # Ripristino
            page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1000)
            
            indietro = page.query_selector("a:has-text('Indietro'), button:has-text('Indietro')")
            if indietro:
                indietro.click()
                page.wait_for_timeout(3000)
            
            page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            
            # Ora REDD
            log.info(f"\n=== Ric=REDD Anno={ANNO} ===")
            page.goto(f"https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=REDD&Anno={ANNO}",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            
            text = page.inner_text("body")
            log.info(f"  Contenuto:\n{text[:2000]}")
            
            # Salva HTML
            html = page.content()
            with open(f"/tmp/test_redd_{CF_CLIENTE}_{ANNO}.html", "w") as f:
                f.write(html)
            log.info(f"  HTML salvato ({len(html)} byte)")
            
            # Cerca PDF link
            pdf_links = page.query_selector_all("a[href*='pdf' i], a[href*='stampa'], a[href*='download']")
            log.info(f"  Trovati {len(pdf_links)} link potenziali PDF")
            for link in pdf_links[:10]:
                href = link.get_attribute("href") or ""
                txt = link.inner_text().strip()
                log.info(f"    '{txt}' -> {href[:100]}")
    
    # Step 3: Prova anche con Ric=730
    if found:
        log.info(f"\n=== Ric=730 Anno={ANNO} ===")
        page.goto(f"https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=730&Anno={ANNO}",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        text = page.inner_text("body")
        log.info(f"  Contenuto:\n{text[:1500]}")

finally:
    engine.close()
