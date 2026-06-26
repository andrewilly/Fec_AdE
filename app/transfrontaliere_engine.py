from typing import Any, Dict, List
import requests
from app.engine import get_date_chunks, unix_ms
from app.security import get_ca_bundle

BASE = "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs/ft"
STATO_ATTIVA = "TRANSFRONTALIERE_ATTIVA"


def fetch(
    session: requests.Session,
    headers_cons: Dict[str, str],
    dal: str,
    al: str,
    category: str,
    piva: str = "",
    stato: str = STATO_ATTIVA,
    logger=print,
) -> Dict[str, Any]:
    if category not in ("EMESSE", "RICEVUTE"):
        raise ValueError(f"category invalida: {category}")

    chunks = get_date_chunks(dal, al)
    all_invoices: List[Dict[str, Any]] = []
    seen = set()

    logger(f"Ricerca TRANSFRONTALIERE_{category} stato={stato}...")
    for d_dal, d_al in chunks:
        dal_c = d_dal.replace("/", "")
        al_c = d_al.replace("/", "")
        url = (
            f"{BASE}/{category.lower()}/dal/{dal_c}/al/{al_c}"
            f"/stato/{stato}?v={unix_ms()}"
        )

        try:
            r = session.get(url, headers=headers_cons, verify=get_ca_bundle())
        except Exception as e:
            logger(f"  TF {category} errore rete chunk {dal_c}-{al_c}: {e}")
            continue

        if r.status_code != 200:
            logger(f"  TF {category} HTTP {r.status_code} chunk {dal_c}-{al_c}")
            continue

        try:
            data = r.json()
        except Exception as e:
            logger(f"  TF {category} JSON malformato chunk {dal_c}-{al_c}: {e}")
            continue

        errors = [
            m for m in (data.get("messages") or [])
            if isinstance(m, dict) and m.get("severity") == "ERROR"
        ]
        if errors:
            logger(
                f"  TF {category} API error chunk {dal_c}-{al_c}: "
                f"{errors[0].get('message', '?')}"
            )
            continue

        for inv in data.get("fatture") or []:
            uid = f"{inv.get('idFattura')}_{inv.get('tipoInvio')}"
            if uid in seen:
                continue
            seen.add(uid)
            all_invoices.append(inv)

    logger(f"  TF {category}: {len(all_invoices)} fatture trovate")
    return {"totaleFatture": len(all_invoices), "fatture": all_invoices}
