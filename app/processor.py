"""
Parsing XML fattura elettronica e importazione nel database.

Estrae i dati strutturati da file XML FatturaPA (o transfrontalieri)
e li inserisce nel database SQLite/MySQL tramite i modelli ORM.
"""

import os
from lxml import etree
from sqlalchemy import func
from typing import Optional

import app.database as database
from app.database import (
    DatiGenerali, Anagrafica, RigheFattura,
    DatiRiferimento, DatiDDT, DatiRiepilogo, DatiPagamento, DettaglioPagamento,
)
from app.log_config import get_logger

_log = get_logger("processor")
_DB_INITIALIZED = False


def get_text(element, xpath_local) -> Optional[str]:
    """Estrae il testo da un elemento XML tramite xpath locale (senza namespace)."""
    res = element.xpath(xpath_local)
    return res[0].text if res and res[0].text else None


def clean_float(val: Optional[str]) -> float:
    """
    Converte una stringa importo in float.
    Restituisce 0.0 se il parsing fallisce.
    """
    if not val:
        return 0.0
    try:
        cleaned = val.strip()
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        return float(cleaned)
    except (ValueError, AttributeError):
        return 0.0


def get_or_create_anagrafica(db, element):
    """Recupera o crea un record anagrafica da un elemento XML."""
    id_paese = get_text(element, ".//*[local-name()='IdPaese']")
    id_codice = get_text(element, ".//*[local-name()='IdCodice']")
    cf = get_text(element, ".//*[local-name()='CodiceFiscale']")

    piva = f"{id_paese}{id_codice}" if id_paese and id_codice else None
    id_fiscale = piva if piva else cf

    if not id_fiscale:
        return None

    existing = db.query(Anagrafica).filter(Anagrafica.id_fiscale == id_fiscale).first()
    if existing:
        return existing

    denominazione = get_text(element, ".//*[local-name()='Denominazione']")
    if not denominazione:
        nome = get_text(element, ".//*[local-name()='Nome']")
        cognome = get_text(element, ".//*[local-name()='Cognome']")
        if nome and cognome:
            denominazione = f"{nome} {cognome}"

    new_anag = Anagrafica(
        id_fiscale=id_fiscale, piva=piva, cf=cf, denominazione=denominazione,
        indirizzo=get_text(element, ".//*[local-name()='Indirizzo']"),
        comune=get_text(element, ".//*[local-name()='Comune']"),
        cap=get_text(element, ".//*[local-name()='CAP']"),
        nazione=get_text(element, ".//*[local-name()='Nazione']"),
    )
    db.add(new_anag)
    db.flush()
    return new_anag


def _infer_transfrontaliera(file_path: str) -> int:
    """
    Determina se un file è transfrontaliero basandosi sul path.
    0 = nazionale, 1 = emessa transfrontaliera, 2 = ricevuta transfrontaliera.

    Gestisce sia separatori Unix (/) che Windows (\\) per portabilità.
    """
    # Normalizza i separatori Windows in Unix, poi splitta
    normalized = os.path.normpath(file_path).replace("\\", "/")
    parts = set(p.upper() for p in normalized.split("/"))
    if "EMESSE_TRANSFRONTALIERE" in parts:
        return 1
    if "RICEVUTE_TRANSFRONTALIERE" in parts:
        return 2
    return 0


def process_xml_file(file_path: str, data_ricezione: Optional[str] = None) -> str:
    """
    Processa un file XML di fattura elettronica e lo importa nel database.

    Args:
        file_path: path assoluto o relativo al file XML
        data_ricezione: data di ricezione (per fatture ricevute)

    Returns:
        "ADDED" se inserita, "SKIPPED" se già presente, "ERROR" se errore
    """
    global _DB_INITIALIZED

    if not os.path.exists(file_path):
        return "ERROR"

    if not _DB_INITIALIZED:
        database.init_db()
        _DB_INITIALIZED = True

    db = database.get_session_factory()()
    nome_file = os.path.basename(file_path)

    try:
        tree = etree.parse(file_path)
        root = tree.getroot()

        # Controllo duplicati
        existing_fat = (
            db.query(DatiGenerali)
            .filter(func.lower(DatiGenerali.nome_file) == nome_file.lower())
            .first()
        )
        if existing_fat:
            _log.info("[DB] Salto (già presente): %s", nome_file)
            return "SKIPPED"

        # Anagrafiche
        cedente_el = root.xpath("//*[local-name()='CedentePrestatore']")[0]
        cessionario_el = root.xpath("//*[local-name()='CessionarioCommittente']")[0]
        cedente = get_or_create_anagrafica(db, cedente_el)
        cessionario = get_or_create_anagrafica(db, cessionario_el)

        paese_cedente = get_text(cedente_el, ".//*[local-name()='IdPaese']")
        paese_cessionario = get_text(cessionario_el, ".//*[local-name()='IdPaese']")

        # Dati generali documento
        dati_gen_el = root.xpath("//*[local-name()='DatiGeneraliDocumento']")[0]

        causali_el = dati_gen_el.xpath(".//*[local-name()='Causale']")
        causale_text = " | ".join([c.text for c in causali_el if c.text]) if causali_el else None

        data_fattura = get_text(dati_gen_el, ".//*[local-name()='Data']")
        data_ricezione_finale = data_ricezione if data_ricezione else data_fattura

        new_fat = DatiGenerali(
            nome_file=nome_file,
            tipo_documento=get_text(dati_gen_el, ".//*[local-name()='TipoDocumento']"),
            divisa=get_text(dati_gen_el, ".//*[local-name()='Divisa']"),
            data=data_fattura,
            numero=get_text(dati_gen_el, ".//*[local-name()='Numero']"),
            data_ricezione=data_ricezione_finale,
            importo_totale=clean_float(get_text(dati_gen_el, ".//*[local-name()='ImportoTotaleDocumento']")),
            arrotondamento=clean_float(get_text(dati_gen_el, ".//*[local-name()='Arrotondamento']")),
            causale=causale_text,
            transfrontaliera=_infer_transfrontaliera(file_path),
            paese_cedente=paese_cedente,
            paese_cessionario=paese_cessionario,
            id_cedente=cedente.id if cedente else None,
            id_cessionario=cessionario.id if cessionario else None,
        )
        db.add(new_fat)
        db.flush()

        # Righe fattura
        linea_to_id = {}
        righe_el = root.xpath("//*[local-name()='DettaglioLinee']")
        for r_el in righe_el:
            num_linea_str = get_text(r_el, ".//*[local-name()='NumeroLinea']")
            num_linea = int(num_linea_str) if num_linea_str else 0

            new_riga = RigheFattura(
                id_fattura=new_fat.id,
                numero_linea=num_linea,
                descrizione=get_text(r_el, ".//*[local-name()='Descrizione']"),
                quantita=clean_float(get_text(r_el, ".//*[local-name()='Quantita']")),
                prezzo_unitario=clean_float(get_text(r_el, ".//*[local-name()='PrezzoUnitario']")),
                prezzo_totale=clean_float(get_text(r_el, ".//*[local-name()='PrezzoTotale']")),
                aliquota_iva=clean_float(get_text(r_el, ".//*[local-name()='AliquotaIVA']")),
            )
            db.add(new_riga)
            db.flush()
            if num_linea > 0:
                linea_to_id[num_linea] = new_riga.id

        # Riferimenti
        ref_mappings = {
            'DatiOrdineAcquisto': 'ORDINE',
            'DatiContratto': 'CONTRATTO',
            'DatiConvenzione': 'CONVENZIONE',
            'DatiFattureCollegate': 'FATTURE_COLLEGATE',
        }
        for tag, label in ref_mappings.items():
            refs = root.xpath(f"//*[local-name()='{tag}']")
            for ref in refs:
                ref_linea_str = get_text(ref, ".//*[local-name()='RiferimentoNumeroLinea']")
                ref_linea = int(ref_linea_str) if ref_linea_str else None

                db.add(DatiRiferimento(
                    id_fattura=new_fat.id, tipo=label,
                    riferimento_numero_linea=ref_linea,
                    id_documento=get_text(ref, ".//*[local-name()='IdDocumento']"),
                    data=get_text(ref, ".//*[local-name()='Data']"),
                    codice_commessa=get_text(ref, ".//*[local-name()='CodiceCommessaConvenzione']"),
                    codice_cup=get_text(ref, ".//*[local-name()='CodiceCUP']"),
                    codice_cig=get_text(ref, ".//*[local-name()='CodiceCIG']"),
                    id_riga_db=linea_to_id.get(ref_linea) if ref_linea else None,
                ))

        # DDT
        ddts = root.xpath("//*[local-name()='DatiDDT']")
        for ddt in ddts:
            ref_linea_str = get_text(ddt, ".//*[local-name()='RiferimentoNumeroLinea']")
            ref_linea = int(ref_linea_str) if ref_linea_str else None
            db.add(DatiDDT(
                id_fattura=new_fat.id,
                numero_ddt=get_text(ddt, ".//*[local-name()='NumeroDDT']"),
                data_ddt=get_text(ddt, ".//*[local-name()='DataDDT']"),
                riferimento_numero_linea=ref_linea,
                id_riga_db=linea_to_id.get(ref_linea) if ref_linea else None,
            ))

        # Riepiloghi IVA
        riepiloghi = root.xpath("//*[local-name()='DatiRiepilogo']")
        for riep in riepiloghi:
            db.add(DatiRiepilogo(
                id_fattura=new_fat.id,
                aliquota_iva=clean_float(get_text(riep, ".//*[local-name()='AliquotaIVA']")),
                natura=get_text(riep, ".//*[local-name()='Natura']"),
                spese_accessorie=clean_float(get_text(riep, ".//*[local-name()='SpeseAccessorie']")),
                arrotondamento=clean_float(get_text(riep, ".//*[local-name()='Arrotondamento']")),
                imponibile_importo=clean_float(get_text(riep, ".//*[local-name()='ImponibileImporto']")),
                imposta=clean_float(get_text(riep, ".//*[local-name()='Imposta']")),
                esigibilita_iva=get_text(riep, ".//*[local-name()='EsigibilitaIVA']"),
                riferimento_normativo=get_text(riep, ".//*[local-name()='RiferimentoNormativo']"),
            ))

        # Pagamenti
        pag_list = root.xpath("//*[local-name()='DatiPagamento']")
        for pag in pag_list:
            new_pag = DatiPagamento(
                id_fattura=new_fat.id,
                condizioni_pagamento=get_text(pag, ".//*[local-name()='CondizioniPagamento']"),
            )
            db.add(new_pag)
            db.flush()

            dettagli = pag.xpath(".//*[local-name()='DettaglioPagamento']")
            for det in dettagli:
                db.add(DettaglioPagamento(
                    id_pagamento=new_pag.id,
                    modalita_pagamento=get_text(det, ".//*[local-name()='ModalitaPagamento']"),
                    data_scadenza=get_text(det, ".//*[local-name()='DataScadenzaPagamento']"),
                    importo=clean_float(get_text(det, ".//*[local-name()='ImportoPagamento']")),
                    iban=get_text(det, ".//*[local-name()='IBAN']"),
                    abi=get_text(det, ".//*[local-name()='ABI']"),
                    cab=get_text(det, ".//*[local-name()='CAB']"),
                    bic=get_text(det, ".//*[local-name()='BIC']"),
                ))

        db.commit()
        _log.info("[DB] Salvata fattura: %s", nome_file)
        return "ADDED"

    except Exception as e:
        db.rollback()
        _log.error("[DB] Errore in %s: %s", nome_file, e)
        return "ERROR"
    finally:
        db.close()
