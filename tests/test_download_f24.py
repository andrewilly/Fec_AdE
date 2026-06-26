#!/usr/bin/env python3
"""Test download F24 PDF dopo cambio cliente via PortaleWeb."""
import logging
import os, sys, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_download")

from app.config import config
config.load()
CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")
PIVA_CLIENTE = "17836421002"

from app.cassetto_fiscale_browser import CassettoFiscaleBrowserEngine

engine = CassettoFiscaleBrowserEngine(logger=log.info, headless=True)
try:
    # Step 1: Login + init Cassetto (come intermediario)
    engine.init_session(CF, PIN, PASSWORD)
    page = engine._page
    
    # Step 2: Cambio utenza su PortaleWeb
    log.info("=== Cambio utenza via PortaleWeb ===")
    page.goto("https://portale.agenziaentrate.gov.it/PortaleWeb/cambiautenza",
              wait_until="networkidle", timeout=30000)
    
    # Seleziona radio "Incaricato"
    for rb in page.query_selector_all("input[name='ruoloType']"):
        rid = rb.get_attribute('id')
        label = page.evaluate(f'document.querySelector(\'label[for="{rid}"]\')?.innerText')
        if label and "incaricato" in label.lower():
            rb.click()
            break
    
    page.wait_for_timeout(500)
    
    # Seleziona PIVA
    page.select_option("select[name='utenza']", PIVA_CLIENTE)
    page.wait_for_timeout(500)
    
    # Click Conferma
    page.click("button:has-text('Conferma')")
    page.wait_for_timeout(3000)
    log.info(f"  Confermato, ora su: {page.url}")
    
    # Step 3: Ripristino Cassetto Fiscale
    log.info("=== Ripristino Cassetto ===")
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1000)
    
    # Click "Indietro" per ripristino
    indietro = page.query_selector("a:has-text('Indietro'), button:has-text('Indietro')")
    if indietro:
        indietro.click()
        page.wait_for_timeout(3000)
        log.info(f"  Dopo Indietro: {page.url}")
    
    # Torna a HOME
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=HOME",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    
    # Re-init (non serve sempre, ma per sicurezza)
    page.evaluate("fetch('https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initLight?v=' + Date.now())")
    page.evaluate("""async () => { 
        await fetch('https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initCassetto?v=' + Date.now(), {
            method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
        }); 
    }""")
    page.wait_for_timeout(1000)
    
    # Step 4: Naviga a VERS/2026
    log.info("=== VERS/2026 ===")
    page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=VERS&Anno=2026",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    
    # Estrai link PDF dalla pagina
    log.info("=== Cerca link PDF quietanza ===")
    pdf_links = page.query_selector_all("a[href*='stampa=Q']")
    log.info(f"  Trovati {len(pdf_links)} link PDF quietanza")
    
    for i, link in enumerate(pdf_links):
        href = link.get_attribute("href") or ""
        log.info(f"\n  Link {i}: {href}")
        
        # Costruisci URL completo
        if href.startswith("/"):
            pdf_url = f"https://cassetto.agenziaentrate.gov.it{href}"
        else:
            pdf_url = href
        
        log.info(f"  URL completo: {pdf_url}")
        
        # Scarica via browser usando Playwright download API
        log.info(f"  Download PDF via click su link + expect_download...")
        
        # Torna alla pagina VERS/2026 (dopo ogni download siamo altrove)
        page.goto("https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet?Ric=VERS&Anno=2026",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        
        # Trova il link con stampa=Q specifico
        pdf_link = page.query_selector(f"a[href*='stampa=Q'][href*='indice={i}']")
        if not pdf_link:
            pdf_link = page.query_selector("a[href*='stampa=Q']")
        
        if pdf_link:
            # Usa expect_download PRIMA del click
            with page.expect_download(timeout=30000) as download_info:
                pdf_link.click()
            
            download = download_info.value
            log.info(f"  Download: {download.suggested_filename}")
            
            # Salva
            download_path = f"/tmp/{download.suggested_filename}"
            download.save_as(download_path)
            log.info(f"  Salvato in: {download_path}")
            
            # Verifica
            with open(download_path, "rb") as f:
                header = f.read(5)
            is_pdf = header == b"%PDF-"
            file_size = os.path.getsize(download_path)
            log.info(f"  Dimensione: {file_size} bytes, PDF magic={is_pdf}")
            
            if is_pdf:
                log.info(f"  ✅ PDF VALIDO: {download_path}")
            else:
                log.info(f"  ⚠️ NON è PDF (header={header})")
                # Leggi prime righe per debug
                with open(download_path, "r") as f:
                    log.info(f"  Contenuto: {f.read()[:200]}")
        else:
            log.info(f"  Link PDF non trovato per indice={i}")
    
    # Step 5: Prova anche DetF24 (dettaglio HTML)
    if pdf_links:
        log.info("\n=== Prova DetF24 senza stampa (dettaglio HTML) ===")
        href = pdf_links[0].get_attribute("href") or ""
        # Rimuovi &stampa=Q
        href_no_q = href.replace("&stampa=Q", "")
        if href_no_q.startswith("/"):
            url_no_q = f"https://cassetto.agenziaentrate.gov.it{href_no_q}"
        else:
            url_no_q = href_no_q
        log.info(f"  URL dettaglio: {url_no_q}")
        
        resp = page.goto(url_no_q, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
        log.info(f"  Dettaglio HTML:\n{text[:1000]}")

finally:
    engine.close()
