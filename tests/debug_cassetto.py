#!/usr/bin/env python3
"""
Debug: esplora la HOME page del Cassetto Fiscale via browser.
Cerca form di cambio cliente, campi di ricerca, etc.
"""
import logging, sys, os, json, time
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("debug_cassetto")

from app.config import config
config.load()

CF = config.get("CF", "")
PIN = config.get("PIN", "")
PASSWORD = config.get("PASSWORD", "")
PIVA_TARGET = sys.argv[1] if len(sys.argv) > 1 else "17836421002"

from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="it-IT")
page = ctx.new_page()

# 1. Login SSO
SSO_URL = ("https://iampe.agenziaentrate.gov.it/sam/UI/Login"
           "?realm=/agenziaentrate"
           "&goto=https%3A%2F%2Fportale.agenziaentrate.gov.it%3A443%2FPortaleWeb%2Fhome")
page.goto(SSO_URL, wait_until="networkidle", timeout=60000)
page.locator("text=Fisconline/Entratel").first.click()
page.wait_for_timeout(1000)
page.locator("#username-fo-ent").fill(CF)
page.locator("#password-fo-ent-1").fill(PASSWORD)
page.locator("#pin-fo-ent").fill(PIN)
page.locator("button:has-text('Accedi')").first.click()
page.wait_for_url(lambda u: u.startswith("https://portale.agenziaentrate.gov.it"), timeout=30000)
log.info(f"Login OK -> {page.url}")

# 2. Vai al Cassetto Fiscale
page.goto("https://cassetto.agenziaentrate.gov.it/CassHomeWeb/home",
          wait_until="networkidle", timeout=30000)
log.info(f"Cassetto: {page.title()}")

# 3. Esplora la pagina: cerca input, form, bottoni
page.wait_for_timeout(3000)
html = page.content()
log.info(f"HTML size: {len(html)} bytes")

# Salva HTML per ispezione
with open("/tmp/cassetto_home.html", "w") as f:
    f.write(html)
log.info("HTML salvato in /tmp/cassetto_home.html")

# Cerca input visibili
inputs = page.locator("input:visible, select:visible, textarea:visible").all()
log.info(f"\nInput visibili: {len(inputs)}")
for inp in inputs:
    tag = inp.evaluate("e => e.tagName")
    name = inp.get_attribute("name") or ""
    id = inp.get_attribute("id") or ""
    placeholder = inp.get_attribute("placeholder") or ""
    type = inp.get_attribute("type") or ""
    log.info(f"  <{tag}> name={name} id={id} type={type} placeholder='{placeholder}'")

# Cerca link e bottoni
links = page.locator("a:visible, button:visible").all()
log.info(f"\nLink/bottoni visibili: {len(links)}")
for a in links:
    text = (a.text_content() or "").strip()
    href = a.get_attribute("href") or ""
    if text and len(text) < 50:
        log.info(f"  '{text[:40]}' href={href[:80]}")

# 4. Prova a navigare direttamente alla servlet F24 per TARGET
CASSETTO_SERVLET = "https://cassetto.agenziaentrate.gov.it/cassfisc-web/CassettoFiscaleServlet"
f24_url = f"{CASSETTO_SERVLET}?Ric=F24&Anno=2026&pIva={PIVA_TARGET}&cf={PIVA_TARGET}"
log.info(f"\nNavigo direttamente a F24 servlet con params...")
page.goto(f24_url, wait_until="networkidle", timeout=30000)
log.info(f"URL: {page.url}")
log.info(f"Titolo: {page.title()}")
f24_html = page.content()
log.info(f"HTML size: {len(f24_html)} bytes")
with open("/tmp/cassetto_f24_direct.html", "w") as f:
    f.write(f24_html)
log.info("HTML salvato in /tmp/cassetto_f24_direct.html")

# Cerca "F24" nel testo
body_text = page.locator("body").text_content()[:2000]
log.info(f"Body text: {body_text[:1000]}")

# 5. Prova cambiaCliente API via browser evaluate
log.info("\nProvo cambiaCliente API...")
result = page.evaluate("""
    async (piva) => {
        const r = await fetch('https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/cambiaCliente?v=' + Date.now(), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({pIva: piva})
        });
        return {status: r.status, body: await r.text()};
    }
""", PIVA_TARGET)
log.info(f"cambiaCliente({PIVA_TARGET}): HTTP {result['status']} | {result['body'][:100]}")

# 6. Se 409, prova con altri body
if result['status'] == 409:
    result2 = page.evaluate("""
        async (cf) => {
            const r = await fetch('https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/cambiaCliente?v=' + Date.now(), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({cf: cf})
            });
            return {status: r.status, body: await r.text()};
        }
    """, PIVA_TARGET)
    log.info(f"cambiaCliente(cf={PIVA_TARGET}): HTTP {result2['status']} | {result2['body'][:100]}")

# 7. Dopo tentativo cambio, naviga di nuovo alla servlet F24
log.info("\nRiprovo servlet F24 dopo cambio...")
page.goto(f24_url, wait_until="networkidle", timeout=30000)
f24_html2 = page.content()
log.info(f"HTML size: {len(f24_html2)} bytes")
body_text2 = page.locator("body").text_content()[:2000]
log.info(f"Body: {body_text2[:1000]}")

# Cerca "Nessun documento" o "documento" nel testo
if "Nessun documento" in body_text2:
    log.info("⚠️ 'Nessun documento' trovato — nessun F24 per questo cliente/anno")
if "F24" in body_text2:
    log.info("✅ 'F24' trovato nel body!")

browser.close()
pw.stop()
