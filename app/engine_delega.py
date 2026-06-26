from typing import Any, Callable, Dict
import requests


class DelegaDirettaEngine:
    def __init__(
        self,
        session: requests.Session,
        logger_func,
        wizard_set_user_choice_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        extract_piva_func: Callable[[Dict[str, Any], str], str],
    ):
        self.session = session
        self.logger = logger_func
        self._wizard_set_user_choice = wizard_set_user_choice_func
        self._extract_piva_value = extract_piva_func

    def run_selection(self, p_auth: str, piva: str) -> str:
        _ = p_auth
        self.logger("Inizio procedura 'Delega Diretta'...")

        payload = {
            "tipoutenza": "delegaDiretta",
            "tipoDelega": "delDiretta",
            "cf": piva,
        }
        data = self._wizard_set_user_choice(payload)
        confirmed_piva = self._extract_piva_value(data, piva.strip())
        if not confirmed_piva:
            raise Exception(
                f"PIVA non confermata dal wizard per utenza 'DELEGA_DIRETTA'. "
                f"Verifica che il CF {piva} abbia una delega attiva."
            )
        self.logger(f"PIVA confermata dal wizard: {confirmed_piva}")
        return confirmed_piva
