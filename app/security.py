"""
Security utilities per Fec_AdE.

Centralizza la verifica SSL/TLS e la gestione dei certificati.
TUTTE le chiamate HTTP devono usare `verify=CA_BUNDLE` invece di `verify=False`.
"""

import os
from typing import Optional

try:
    import certifi
    CA_BUNDLE: Optional[str] = certifi.where()
except ImportError:
    CA_BUNDLE = None

# Percorso per un bundle CA personalizzato (se il certifi non funziona con l'AE)
CUSTOM_CA_BUNDLE: Optional[str] = os.environ.get("FEC_CA_BUNDLE")


def get_ca_bundle() -> str:
    """
    Restituisce il path al bundle CA da usare nelle chiamate requests.

    Ordine di priorità:
      1. Variabile d'ambiente FEC_CA_BUNDLE
      2. certifi.where() (bundle Mozilla standard)
      3. Fallback: True (requests userà il bundle di sistema)
    """
    if CUSTOM_CA_BUNDLE and os.path.isfile(CUSTOM_CA_BUNDLE):
        return CUSTOM_CA_BUNDLE
    if CA_BUNDLE and os.path.isfile(CA_BUNDLE):
        return CA_BUNDLE
    # Fallback: lascia che requests usi il bundle di sistema
    return True  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════════
# NOTA SULLA VERIFICA SSL
# ═══════════════════════════════════════════════════════════════════════════
# In questo progetto TUTTE le chiamate HTTP usavano verify=False,
# il che espone a rischio MITM (Man-In-The-Middle).
#
# L'Agenzia delle Entrate utilizza certificati rilasciati da:
#   - InfoCert per ivaservizi.agenziaentrate.gov.it
#   - Actalis per portale.agenziaentrate.gov.it
#
# Entrambi sono inclusi nel bundle CA di certifi (Mozilla).
# Se dovessi incontrare errori SSL, verifica con:
#   python -c "import requests; print(requests.get('https://ivaservizi.agenziaentrate.gov.it', verify=True))"
#
# In caso di problemi con il bundle Mozilla, SCARICA il certificato specifico:
#   - Vai su https://ivaservizi.agenziaentrate.gov.it con il browser
#   - Esporta il certificato in formato PEM
#   - Salva in ~/.fec_ade/ae-ca.pem
#   - Imposta FEC_CA_BUNDLE=~/.fec_ade/ae-ca.pem
# ═══════════════════════════════════════════════════════════════════════════

__all__ = ["get_ca_bundle", "CA_BUNDLE", "CUSTOM_CA_BUNDLE"]
