import os
import re
import base64
import time
from datetime import datetime
from typing import Dict, Any, Optional
from asn1crypto import cms
from app.processor import process_xml_file

# Mappa categorie inglese → nome cartella italiano (nuova struttura)
CATEGORY_FOLDER_MAP: Dict[str, str] = {
    "RICEVUTE": "fatturericevute",
    "EMESSE": "fattureemesse",
    "RICEVUTE_TRANSFRONTALIERE": "transfrontaliere_ricevute",
    "EMESSE_TRANSFRONTALIERE": "transfrontaliere_emesse",
}

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def unix_ms() -> str:
    from app.engine import unix_ms as engine_unix_ms
    return engine_unix_ms()


def ensure_dirs(*paths: str) -> None:
    for p in paths:
        os.makedirs(p, exist_ok=True)


def safe_filename_from_disposition(disposition: str, fallback: str) -> str:
    m = re.findall(r"filename=(.+)", disposition or "")
    return m[0].strip('"') if m else fallback


def extract_xml_from_p7m(content: bytes, filename: str, logger_func) -> Optional[bytes]:
    try:
        try:
            content_info = cms.ContentInfo.load(content)
        except Exception:
            if content.strip().startswith(b"<"):
                return content
            decoded = base64.b64decode(content)
            content_info = cms.ContentInfo.load(decoded)

        payload = content_info["content"]["encap_content_info"]["content"].native
        return payload

    except Exception as e:
        logger_func(f"Errore estrazione P7M per il file {filename}: {e}")
        return None


class OutputManager:
    """Gestisce il salvataggio su disco delle fatture scaricate.

    Nuova struttura cartelle (Fase 2):
        output/{PIVA}/{ANNO}/{categoria}/
            ORIGINALI/   (file P7M firmati)
            INFO/        (metadati JSON)
            FATTURE/     (XML estratti)

    Dove {categoria} segue la mappa CATEGORY_FOLDER_MAP
    (es. ``RICEVUTE`` → ``fatturericevute``).
    """

    def __init__(self, piva: str, logger_func, anno: int, db_enabled: bool = True):
        self.piva = piva
        self.anno = anno
        self.logger = logger_func
        self.db_enabled = db_enabled
        self.root_path = os.path.join("output", piva, str(anno))
        ensure_dirs(self.root_path)

        self.db_stats = {
            "ADDED": 0,
            "SKIPPED": 0,
            "ERROR": 0
        }

    @staticmethod
    def _category_folder(category: str) -> str:
        """Restituisce il nome cartella italiano per una categoria inglese."""
        return CATEGORY_FOLDER_MAP.get(category, category.lower())

    def _handle_db_hook(self, xml_path: str, data_ricezione=None):
        if self.db_enabled:
            status = process_xml_file(xml_path, data_ricezione=data_ricezione)
            if status in self.db_stats:
                self.db_stats[status] += 1

    def download_invoices_set(
        self,
        session,
        data: Dict[str, Any],
        category: str,
        headers_token: Dict[str, str],
        unix_ms_func
    ) -> Dict[str, Any]:

        cat_folder = self._category_folder(category)
        base_path = os.path.join(self.root_path, cat_folder)
        path_orig = os.path.join(base_path, "ORIGINALI")
        path_info = os.path.join(base_path, "INFO")
        path_fatt = os.path.join(base_path, "FATTURE")
        ensure_dirs(path_orig, path_info, path_fatt)

        total = int(data.get("totaleFatture", 0))
        stats: Dict[str, Any] = {
            "found": total,
            "downloaded": 0,
            "failed": [],
            "failed_struct": [],
            "p7m_errors": []
        }

        self.logger(f"Inizio download {category}: {total} fatture trovate.")

        invoices = data.get("fatture", [])
        iterator = enumerate(invoices, 1)
        if tqdm:
            iterator = tqdm(iterator, total=total, desc=f"Download {category}", unit="fatt", ascii=True)

        def get_with_retry(url: str, headers: Dict[str, str], attempts: int = 3, delay_s: float = 2.0):
            """GET con retry su errori server 5xx e 304."""
            RETRY_STATUSES = {304, 500, 502, 503, 504}
            last_resp = None
            for attempt in range(1, attempts + 1):
                last_resp = session.get(url, headers=headers, stream=True)
                if last_resp.status_code not in RETRY_STATUSES:
                    return last_resp
                if attempt < attempts:
                    self.logger(
                        f"  [RETRY {attempt}/{attempts - 1}] Status {last_resp.status_code} "
                        f"-> riprovo tra {delay_s * attempt:.0f}s..."
                    )
                    time.sleep(delay_s * attempt)
            return last_resp

        for _, fattura in iterator:
            fattura_file = f"{fattura['tipoInvio']}{fattura['idFattura']}"
            data_ricezione = fattura.get("dataConsegna") if category == "RICEVUTE" else None

            try:
                url = (
                    "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs/fatture/file/"
                    f"{fattura_file}?tipoFile=FILE_FATTURA&download=1&v={unix_ms_func()}"
                )
                r = get_with_retry(url, headers_token)

                if r.status_code != 200:
                    fail_struct = {
                        "idFattura": fattura.get("idFattura"),
                        "tipoInvio": fattura.get("tipoInvio"),
                        "status": r.status_code,
                        "url": url,
                        "category": category,
                        "tipoFile": "FILE_FATTURA",
                    }
                    fail_msg = (
                        f"{fattura_file} (Status {r.status_code}) "
                        f"idFattura={fattura.get('idFattura')} tipoInvio={fattura.get('tipoInvio')} "
                        f"url={url}"
                    )
                    stats["failed"].append(fail_msg)
                    stats["failed_struct"].append(fail_struct)
                    self.logger(f"  [DOWNLOAD KO] {fail_msg}")
                    continue

                fname = safe_filename_from_disposition(r.headers.get("content-disposition", ""), f"file_{fattura_file}")
                content = r.content

                is_metadata = fname.lower().startswith("informazioni_associate")

                if is_metadata:
                    with open(os.path.join(path_info, fname), "wb") as f:
                        f.write(content)
                else:
                    with open(os.path.join(path_orig, fname), "wb") as f:
                        f.write(content)

                    if fname.lower().endswith(".p7m"):
                        xml_content = extract_xml_from_p7m(content, fname, self.logger)
                        if xml_content:
                            xml_name = fname.lower().replace(".p7m", "")
                            if not xml_name.endswith(".xml"):
                                xml_name += ".xml"
                            xml_path = os.path.join(path_fatt, xml_name)
                            with open(xml_path, "wb") as f:
                                f.write(xml_content)
                            self._handle_db_hook(xml_path, data_ricezione=data_ricezione)
                        else:
                            stats["p7m_errors"].append(fname)

                    elif fname.lower().endswith(".xml"):
                        xml_path = os.path.join(path_fatt, fname)
                        with open(xml_path, "wb") as f:
                            f.write(content)
                        self._handle_db_hook(xml_path, data_ricezione=data_ricezione)

                stats["downloaded"] += 1

            except Exception as e:
                fail_struct = {
                    "idFattura": fattura.get("idFattura"),
                    "tipoInvio": fattura.get("tipoInvio"),
                    "error": str(e),
                    "category": category,
                    "tipoFile": "FILE_FATTURA",
                }
                fail_msg = (
                    f"{fattura_file} (Errore: {e}) "
                    f"idFattura={fattura.get('idFattura')} tipoInvio={fattura.get('tipoInvio')}"
                )
                stats["failed"].append(fail_msg)
                stats["failed_struct"].append(fail_struct)
                self.logger(f"  [DOWNLOAD KO] {fail_msg}")

            try:
                url_meta = (
                    "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs/fatture/file/"
                    f"{fattura_file}?tipoFile=FILE_METADATI&download=1&v={unix_ms_func()}"
                )
                r = get_with_retry(url_meta, headers_token)
                if r.status_code == 200:
                    fname = safe_filename_from_disposition(
                        r.headers.get("content-disposition", ""), f"meta_{fattura_file}"
                    )
                    content = r.content
                    with open(os.path.join(path_info, fname), "wb") as f:
                        f.write(content)
            except Exception:
                pass

        return stats

    def final_check(self, category: str, stats: Dict[str, Any]):
        cat_folder = self._category_folder(category)
        final_dir = os.path.join(self.root_path, cat_folder, "FATTURE")
        orig_dir = os.path.join(self.root_path, cat_folder, "ORIGINALI")

        if not os.path.exists(final_dir):
            self.logger(f"  Cartella {final_dir} non trovata!")
            return

        files_on_disk = [f for f in os.listdir(final_dir) if f.lower().endswith(".xml")]
        orig_files = [f for f in os.listdir(orig_dir)]

        count_disk = len(files_on_disk)

        self.logger(f"\n--- RIEPILOGO {category} ---")
        self.logger(f"Fatture trovate sul portale: {stats['found']}")
        self.logger(f"Fatture scaricate (files):   {stats['downloaded']}")
        self.logger(f"Fatture XML totali su disco: {count_disk}")

        if stats['found'] > count_disk:
            self.logger(f"  ATTENZIONE: Mancano {stats['found'] - count_disk} file XML!")

            missing = []
            for f_orig in orig_files:
                expected_xml = f_orig.lower().replace(".p7m", "").replace(".xml", "") + ".xml"
                if expected_xml not in [fn.lower() for fn in files_on_disk]:
                    missing.append(f_orig)

            if missing:
                self.logger("  Fatture presenti in ORIGINALI ma non convertite in XML:")
                for m in missing:
                    self.logger(f"    - {m}")

        err_count = len(stats.get("failed", [])) + len(stats.get("p7m_errors", []))
        if err_count:
            self.logger(f"Errori riscontrati durante il download: {err_count}")
            for fail in stats.get("failed", []):
                self.logger(f"  [DOWNLOAD KO] {fail}")
            for p7m_fail in stats.get("p7m_errors", []):
                self.logger(f"  [ESTRAZIONE P7M KO] {p7m_fail}")

        if self.db_enabled:
            self.logger("\n--- STATISTICHE DATABASE (Totale sessione) ---")
            self.logger(f"Nuove fatture inserite:   {self.db_stats['ADDED']}")
            self.logger(f"Fatture saltate (dupl.):  {self.db_stats['SKIPPED']}")
            if self.db_stats['ERROR'] > 0:
                self.logger(f"Errori durante l'inserimento: {self.db_stats['ERROR']}")
            else:
                self.logger("Nessun errore riscontrato nel Database.")
