"""
Test per app/deleghe_reader.py — recupero deleghe attive.
"""

import pytest
import os
import tempfile

from app.deleghe_reader import (
    _get_ragione_sociale,
    _extract_incarichi,
    _extract_deleganti,
    load_deleghe_from_csv,
    _carica_mappa_ragioni_sociali,
    salva_ragione_sociale,
    RAGIONI_SOCIALI_FILE,
)


class TestGetRagioneSociale:
    """Test per _get_ragione_sociale()."""

    def test_denominazione(self):
        """Se presente 'denominazione', la restituisce."""
        result = _get_ragione_sociale({"denominazione": "ROSSI MARIO SRL"})
        assert result == "ROSSI MARIO SRL"

    def test_nome_cognome(self):
        """Se manca denominazione, usa nome + cognome."""
        result = _get_ragione_sociale({"nome": "Mario", "cognome": "Rossi"})
        assert result == "Mario Rossi"

    def test_solo_nome(self):
        result = _get_ragione_sociale({"nome": "Mario"})
        assert result == "Mario"

    def test_solo_cognome(self):
        result = _get_ragione_sociale({"cognome": "Rossi"})
        assert result == "Rossi"

    def test_empty(self):
        result = _get_ragione_sociale({})
        assert result == ""

    def test_denominazione_empty_string(self):
        result = _get_ragione_sociale({"denominazione": "  "})
        assert result == ""


class TestExtractIncarichi:
    """Test per _extract_incarichi()."""

    def test_empty_template(self):
        assert _extract_incarichi({}) == []

    def test_no_incarichi(self):
        template = {"template": {"richiestaIncarichi": {"incarichi": []}}}
        assert _extract_incarichi(template) == []

    def test_single_incarico(self):
        template = {
            "template": {
                "richiestaIncarichi": {
                    "incarichi": [
                        {"incaricante": {"cf": "RSSMRA85M01H501Z", "denominazione": "Mario Rossi"}}
                    ]
                }
            }
        }
        result = _extract_incarichi(template)
        assert len(result) == 1
        assert result[0]["incaricante"]["cf"] == "RSSMRA85M01H501Z"

    def test_multiple_incarichi(self):
        template = {
            "richiestaIncarichi": {
                "incarichi": [
                    {"incaricante": {"cf": "AAAAAA00A00A000A"}},
                    {"incaricante": {"cf": "BBBBBB00B00B000B"}},
                ]
            }
        }
        result = _extract_incarichi(template)
        assert len(result) == 2

    def test_nested_and_root(self):
        """Cerca sia in root che in template."""
        template = {
            "richiestaIncarichi": {"incarichi": [{"root": True}]},
            "template": {
                "richiestaIncarichi": {"incarichi": [{"nested": True}]},
            }
        }
        result = _extract_incarichi(template)
        assert len(result) == 2


class TestLoadDelegheFromCsv:
    """Test per load_deleghe_from_csv()."""

    def test_file_not_exists(self):
        """File inesistente → lista vuota."""
        result = load_deleghe_from_csv("/tmp/non_existent_file_12345.csv")
        assert result == []

    def test_valid_csv(self):
        """CSV valido con CF."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("CF;Servizio;Data_inizio;Data_fine\n")
            f.write("RSSMRA85M01H501Z;Fatture;01/01/2025;31/12/2025\n")
            f.write("VRDLSS85M01H501Z;Corrispettivi;01/01/2025;31/12/2025\n")
            csv_path = f.name

        try:
            result = load_deleghe_from_csv(csv_path)
            assert len(result) == 2
            assert result[0]["cf"] == "RSSMRA85M01H501Z"
            assert result[1]["cf"] == "VRDLSS85M01H501Z"
            assert result[0]["tipo"] == "FOL"
        finally:
            os.unlink(csv_path)

    def test_csv_with_denominazione(self):
        """CSV con colonna Denominazione."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("CF;Denominazione;Servizio\n")
            f.write("RSSMRA85M01H501Z;Mario Rossi;Fatture\n")
            csv_path = f.name

        try:
            result = load_deleghe_from_csv(csv_path)
            assert len(result) == 1
            assert result[0]["ragione_sociale"] == "Mario Rossi"
        finally:
            os.unlink(csv_path)

    def test_empty_lines(self):
        """Righe vuote o senza CF vengono ignorate."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("CF;Servizio\n")
            f.write("RSSMRA85M01H501Z;Fatture\n")
            f.write(";\n")
            f.write("\n")
            csv_path = f.name

        try:
            result = load_deleghe_from_csv(csv_path)
            assert len(result) == 1
        finally:
            os.unlink(csv_path)


class TestRagioniSociali:
    """Test per gestione mappa ragioni sociali."""

    def test_carica_mappa_non_existent(self):
        """File non esistente → dict vuoto."""
        result = _carica_mappa_ragioni_sociali()
        assert result == {}

    def test_salva_e_carica(self):
        """Salva e ricarica la mappa."""
        try:
            salva_ragione_sociale("01234567890", "RSSMRA85M01H501Z", "Mario Rossi")
            assert os.path.exists(RAGIONI_SOCIALI_FILE)

            mappa = _carica_mappa_ragioni_sociali()
            assert "01234567890" in mappa
            assert mappa["01234567890"] == "Mario Rossi"
            assert mappa["RSSMRA85M01H501Z"] == "Mario Rossi"
        finally:
            if os.path.exists(RAGIONI_SOCIALI_FILE):
                os.unlink(RAGIONI_SOCIALI_FILE)


@pytest.mark.smoke
def test_deleghe_reader_smoke():
    """Smoke test delle funzioni principali."""
    rs = _get_ragione_sociale({"denominazione": "TEST SRL"})
    assert rs == "TEST SRL"

    incarichi = _extract_incarichi({
        "richiestaIncarichi": {"incarichi": [{"test": True}]}
    })
    assert len(incarichi) == 1

    csv_result = load_deleghe_from_csv("/tmp/nonexistent.csv")
    assert csv_result == []

    deleganti = _extract_deleganti({
        "richiestaIncarichi": {"incarichi": [{"delegante": True}]}
    })
    assert len(deleganti) == 1

    print("Deleghe reader smoke test OK")


# ═══════════════════════════════════════════════════════════════════════════════
# Test per fetch_deleghe_dirette_from_wizard
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractDeleganti:
    """Test per _extract_deleganti()."""

    def test_empty_template(self):
        from app.deleghe_reader import _extract_deleganti
        assert _extract_deleganti({}) == []

    def test_no_deleganti(self):
        from app.deleghe_reader import _extract_deleganti
        template = {"template": {"richiestaIncarichi": {"incarichi": []}}}
        assert _extract_deleganti(template) == []

    def test_single_delegante(self):
        from app.deleghe_reader import _extract_deleganti
        template = {
            "template": {
                "richiestaIncarichi": {
                    "incarichi": [
                        {"incaricante": {"cf": "RSSMRA85M01H501Z", "denominazione": "Mario Rossi"}}
                    ]
                }
            }
        }
        result = _extract_deleganti(template)
        assert len(result) == 1
        assert result[0]["incaricante"]["cf"] == "RSSMRA85M01H501Z"

    def test_multiple_deleganti(self):
        from app.deleghe_reader import _extract_deleganti
        template = {
            "richiestaIncarichi": {
                "incarichi": [
                    {"incaricante": {"cf": "AAAAAA00A00A000A"}},
                    {"incaricante": {"cf": "BBBBBB00B00B000B"}},
                ]
            }
        }
        result = _extract_deleganti(template)
        assert len(result) == 2

    def test_nested_and_root(self):
        from app.deleghe_reader import _extract_deleganti
        """Cerca sia in root che in template."""
        template = {
            "richiestaIncarichi": {"incarichi": [{"root": True}]},
            "template": {
                "richiestaIncarichi": {"incarichi": [{"nested": True}]},
            }
        }
        result = _extract_deleganti(template)
        assert len(result) == 2


class TestFetchDelegheDiretteFromWizard:
    """Test per fetch_deleghe_dirette_from_wizard()."""

    def _mock_wizard_proceed(self, data: dict):
        """Crea un wizard_proceed_func mock che restituisce data."""
        def func(payload):
            return data
        return func

    def test_success(self):
        """Risposta valida con deleganti."""
        from app.deleghe_reader import fetch_deleghe_dirette_from_wizard
        wizard_data = {
            "richiestaIncarichi": {
                "incarichi": [
                    {
                        "incaricante": {
                            "cf": "RSSMRA85M01H501Z",
                            "denominazione": "Mario Rossi",
                            "sede": "",
                        },
                        "pIva": "01234567890",
                    },
                    {
                        "incaricante": {
                            "cf": "VRDLSS85M01H501Z",
                            "denominazione": "Luigi Verdi",
                            "sede": "AAA-999",
                        },
                    },
                ]
            }
        }
        log_messages = []
        result = fetch_deleghe_dirette_from_wizard(
            self._mock_wizard_proceed(wizard_data),
            log_messages.append,
        )
        assert len(result) == 2
        assert result[0]["cf"] == "RSSMRA85M01H501Z"
        assert result[0]["ragione_sociale"] == "Mario Rossi"
        assert result[0]["tipo_delega"] == "DELEGA_DIRETTA"
        assert result[0]["piva"] == "01234567890"
        assert result[1]["cf"] == "VRDLSS85M01H501Z"
        assert result[1]["tipo"] == "ENT"  # sede con "-"
        assert result[1]["tipo_delega"] == "DELEGA_DIRETTA"

    def test_no_incarichi(self):
        """Risposta senza incarichi → lista vuota."""
        from app.deleghe_reader import fetch_deleghe_dirette_from_wizard
        wizard_data = {"richiestaIncarichi": {"incarichi": []}}
        log_messages = []
        result = fetch_deleghe_dirette_from_wizard(
            self._mock_wizard_proceed(wizard_data),
            log_messages.append,
        )
        assert result == []

    def test_exception(self):
        """Il wizard solleva eccezione → lista vuota."""
        from app.deleghe_reader import fetch_deleghe_dirette_from_wizard

        def broken(_):
            raise RuntimeError("errore simulato")

        log_messages = []
        result = fetch_deleghe_dirette_from_wizard(broken, log_messages.append)
        assert result == []
        assert any("errore simulato" in msg for msg in log_messages)

    def test_non_dict_response(self):
        """Risposta non dict → lista vuota."""
        from app.deleghe_reader import fetch_deleghe_dirette_from_wizard
        log_messages = []
        result = fetch_deleghe_dirette_from_wizard(
            self._mock_wizard_proceed([1, 2, 3]),
            log_messages.append,
        )
        assert result == []


class TestFetchAllDeleghe:
    """Test per fetch_all_deleghe()."""

    def _mock_request_with_x_appl(self, method, url, **kwargs):
        """Mock per request_with_x_appl_func (OK)."""
        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "richiestaIncarichi": {
                        "incarichi": [
                            {
                                "incaricante": {
                                    "cf": "RSSMRA85M01H501Z",
                                    "denominazione": "Mario Rossi",
                                    "sede": "",
                                },
                                "pIva": "01234567890",
                            },
                        ]
                    }
                }
        return MockResponse()

    def _mock_request_with_x_appl_empty(self, method, url, **kwargs):
        """Mock per request_with_x_appl_func (vuoto)."""
        class MockResponse:
            status_code = 200
            def json(self):
                return {"richiestaIncarichi": {"incarichi": []}}
        return MockResponse()

    def _mock_wizard_proceed(self, payload):
        """Mock wizard_proceed per delega diretta."""
        return {
            "richiestaIncarichi": {
                "incarichi": [
                    {
                        "incaricante": {
                            "cf": "VRDLSS85M01H501Z",
                            "denominazione": "Luigi Verdi",
                            "sede": "",
                        },
                        "pIva": "99887766554",
                    },
                ]
            }
        }

    def test_both_types(self):
        """Unisce Incaricato e Delega Diretta."""
        from app.deleghe_reader import fetch_all_deleghe
        log_messages = []
        result = fetch_all_deleghe(
            request_with_x_appl_func=self._mock_request_with_x_appl,
            wizard_proceed_func=self._mock_wizard_proceed,
            logger_func=log_messages.append,
        )
        assert len(result) == 2
        incaricati = [d for d in result if d["tipo_delega"] == "INCARICATO"]
        dirette = [d for d in result if d["tipo_delega"] == "DELEGA_DIRETTA"]
        assert len(incaricati) == 1
        assert len(dirette) == 1
        assert incaricati[0]["cf"] == "RSSMRA85M01H501Z"
        assert dirette[0]["cf"] == "VRDLSS85M01H501Z"

    def test_only_incaricato(self):
        """Solo Incaricato (nessuna delega diretta)."""
        from app.deleghe_reader import fetch_all_deleghe

        def empty_wizard(_):
            return {"richiestaIncarichi": {"incarichi": []}}

        log_messages = []
        result = fetch_all_deleghe(
            request_with_x_appl_func=self._mock_request_with_x_appl,
            wizard_proceed_func=empty_wizard,
            logger_func=log_messages.append,
        )
        assert len(result) == 1
        assert result[0]["tipo_delega"] == "INCARICATO"

    def test_only_incaricato_no_wizard_func(self):
        """Nessun wizard_proceed_func → solo Incaricato."""
        from app.deleghe_reader import fetch_all_deleghe
        log_messages = []
        result = fetch_all_deleghe(
            request_with_x_appl_func=self._mock_request_with_x_appl,
            wizard_proceed_func=None,
            logger_func=log_messages.append,
        )
        assert len(result) == 1
        assert result[0]["tipo_delega"] == "INCARICATO"

    def test_dedup_same_cf(self):
        """Stesso CF in entrambe le liste → tenuto solo il primo (incaricato)."""
        from app.deleghe_reader import fetch_all_deleghe

        def wizard_diretta_dup(_):
            return {
                "richiestaIncarichi": {
                    "incarichi": [
                        {
                            "incaricante": {
                                "cf": "RSSMRA85M01H501Z",  # stesso CF dell'incaricato
                                "denominazione": "Mario Rossi (diretta)",
                                "sede": "",
                            },
                            "pIva": "01234567890",
                        },
                    ]
                }
            }

        log_messages = []
        result = fetch_all_deleghe(
            request_with_x_appl_func=self._mock_request_with_x_appl,
            wizard_proceed_func=wizard_diretta_dup,
            logger_func=log_messages.append,
        )
        assert len(result) == 1
        # Il primo è incaricato (quello con la ragione sociale originale)
        assert result[0]["tipo_delega"] == "INCARICATO"
        assert result[0]["ragione_sociale"] == "Mario Rossi"

    def test_fallback_csv(self):
        """Se wizard_proceed_func è None, usa CSV fallback."""
        from app.deleghe_reader import fetch_all_deleghe
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("CF;Denominazione;Servizio\n")
            f.write("CFDIR01A01A000A;Delega Diretta CSV;Fatture\n")
            csv_path = f.name

        try:
            log_messages = []
            result = fetch_all_deleghe(
                request_with_x_appl_func=self._mock_request_with_x_appl_empty,
                wizard_proceed_func=None,
                logger_func=log_messages.append,
                csv_path=csv_path,
            )
            assert len(result) == 1
            assert result[0]["cf"] == "CFDIR01A01A000A"
            assert result[0]["tipo_delega"] == "DELEGA_DIRETTA"
            assert result[0]["piva"] == "CFDIR01A01A000A"
        finally:
            os.unlink(csv_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Test per le nuove funzioni enhanced (probe endpoint + enhanced fetch)
# ═══════════════════════════════════════════════════════════════════════════════


class MockResponse:
    """Mock per response requests con attributi text, status_code, headers."""
    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        import json as _json
        return _json.loads(self.text)


class TestExtractCfListFromResponse:
    """Test per _extract_cf_list_from_response()."""

    def test_empty_response(self):
        from app.deleghe_reader import _extract_cf_list_from_response
        r = MockResponse(200, "")
        assert _extract_cf_list_from_response(r) == []

    def test_empty_text(self):
        from app.deleghe_reader import _extract_cf_list_from_response
        r = MockResponse(200, "   ")
        assert _extract_cf_list_from_response(r) == []

    def test_json_list_direct(self):
        """Array JSON diretto di dict con cf."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = [
            {"cf": "RSSMRA85M01H501Z", "denominazione": "Mario Rossi"},
            {"cf": "VRDLSS85M01H501Z", "denominazione": "Luigi Verdi"},
        ]
        r = MockResponse(200, json_data=data, headers={"Content-Type": "application/json"})
        result = _extract_cf_list_from_response(r)
        assert len(result) == 2
        assert result[0]["cf"] == "RSSMRA85M01H501Z"
        assert result[0]["ragione_sociale"] == "Mario Rossi"

    def test_json_container_deleghe(self):
        """Dict JSON con chiave 'deleghe' contenente lista."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = {
            "deleghe": [
                {"cf": "AAAAAA00A00A000A", "denominazione": "Alpha Srl"},
                {"cf": "BBBBBB00B00B000B", "denominazione": "Beta Spa"},
            ]
        }
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        assert len(result) == 2

    def test_json_container_deleganti(self):
        """Dict JSON con chiave 'deleganti'."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = {
            "deleganti": [
                {"codiceFiscale": "RSSMRA85M01H501Z", "ragioneSociale": "Mario Rossi"},
            ]
        }
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        assert len(result) == 1
        assert result[0]["cf"] == "RSSMRA85M01H501Z"

    def test_json_container_incarichi(self):
        """Dict JSON con chiave 'incarichi' (stessa struttura wizard)."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = {
            "richiestaIncarichi": {
                "incarichi": [
                    {"incaricante": {"cf": "RSSMRA85M01H501Z"}},
                ]
            }
        }
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        # Questa struttura annidata non è supportata direttamente
        # perché _extract_cf_from_json cerca array direttamente sotto
        # le container_keys. "richiestaIncarichi" non è in container_keys.
        assert result == []

    def test_csv_format(self):
        """Risposta in formato CSV."""
        from app.deleghe_reader import _extract_cf_list_from_response
        csv_text = "CF;Denominazione;Servizio\nRSSMRA85M01H501Z;Mario Rossi;Fatture\n"
        r = MockResponse(200, text=csv_text, headers={"Content-Type": "text/csv"})
        result = _extract_cf_list_from_response(r)
        assert len(result) == 1
        assert result[0]["cf"] == "RSSMRA85M01H501Z"
        assert result[0]["ragione_sociale"] == "Mario Rossi"

    def test_codice_fiscale_key(self):
        """Usa chiave 'codiceFiscale' invece di 'cf'."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = [{"codiceFiscale": "RSSMRA85M01H501Z"}]
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        assert len(result) == 1
        assert result[0]["cf"] == "RSSMRA85M01H501Z"

    def test_no_cf_found(self):
        """Nessun CF trovabile → lista vuota."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = [{"nome": "Mario", "cognome": "Rossi"}]
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        assert result == []

    def test_skip_empty_cf(self):
        """CF vuoto o whitespace viene saltato."""
        from app.deleghe_reader import _extract_cf_list_from_response
        data = [
            {"cf": "RSSMRA85M01H501Z"},
            {"cf": "  "},
            {"cf": ""},
        ]
        r = MockResponse(200, json_data=data)
        result = _extract_cf_list_from_response(r)
        assert len(result) == 1

    def test_json_from_text_fallback(self):
        """Test parsing JSON da text quando json() non disponibile."""
        from app.deleghe_reader import _extract_cf_list_from_response
        # Simula response senza metodo json()
        text = '[{"cf": "RSSMRA85M01H501Z", "denominazione": "Test"}]'
        r = MockResponse(200, text=text)
        # Rimuove json_data per forzare parsing da text
        r._json_data = None
        result = _extract_cf_list_from_response(r)
        assert len(result) == 1
        assert result[0]["cf"] == "RSSMRA85M01H501Z"

    def test_string_input(self):
        """Input diretto stringa (senza oggetto response)."""
        from app.deleghe_reader import _extract_cf_list_from_response
        csv_text = "CF;Denominazione\nRSSMRA85M01H501Z;Mario Rossi\n"
        result = _extract_cf_list_from_response(csv_text)
        assert len(result) == 1
        assert result[0]["cf"] == "RSSMRA85M01H501Z"


class TestExtractCfFromCsvText:
    """Test per _extract_cf_from_csv_text()."""

    def test_valid_csv(self):
        from app.deleghe_reader import _extract_cf_from_csv_text
        text = "CF;Denominazione;Servizio\nRSSMRA85M01H501Z;Mario Rossi;Fatture\n"
        result = _extract_cf_from_csv_text(text)
        assert len(result) == 1

    def test_no_cf_column(self):
        from app.deleghe_reader import _extract_cf_from_csv_text
        text = "Nome;Cognome\nMario;Rossi\n"
        result = _extract_cf_from_csv_text(text)
        assert result == []

    def test_multiple_rows(self):
        from app.deleghe_reader import _extract_cf_from_csv_text
        text = "CF;Denominazione\nA;Alpha\nB;Beta\nC;Gamma\n"
        result = _extract_cf_from_csv_text(text)
        assert len(result) == 3

    def test_empty_text(self):
        from app.deleghe_reader import _extract_cf_from_csv_text
        assert _extract_cf_from_csv_text("") == []


class TestFetchAllDelegheEnhanced:
    """Test per fetch_all_deleghe_enhanced()."""

    class MockEngine:
        """Mock per FEScraperEngine."""
        def __init__(self, incaricati_response=None, endpoint_responses=None):
            self.logger = lambda msg: None
            self._request_calls = []
            self._incaricati_response = incaricati_response
            self._endpoint_responses = endpoint_responses or {}
            self.session = None

        def _request_with_x_appl(self, method, url, **kwargs):
            self._request_calls.append((method, url))
            # Se è la chiamata al wizard (incaricato)
            if "procediWizard" in url:
                return self._incaricati_response or MockResponse(
                    200,
                    json_data={
                        "richiestaIncarichi": {
                            "incarichi": [
                                {
                                    "incaricante": {
                                        "cf": "WIZARD01A01A000A",
                                        "denominazione": "Cliente Wizard",
                                        "sede": "",
                                    },
                                    "pIva": "01234567890",
                                },
                            ]
                        }
                    }
                )
            # Per endpoint probe
            for endpoint_url, resp in self._endpoint_responses.items():
                if endpoint_url in url:
                    return resp
            return MockResponse(404, "Not Found")

    def _make_engine_with_endpoint(self, endpoint_url, cf_list):
        """Crea un MockEngine con un endpoint che risponde."""
        from app.deleghe_reader import TIPO_DELEGA_DIRETTA
        data = [{"cf": cf, "denominazione": f"Cliente {cf}"} for cf in cf_list]
        endpoint_resp = MockResponse(200, json_data=data)
        eng = self.MockEngine(
            endpoint_responses={endpoint_url: endpoint_resp}
        )
        return eng

    def test_both_types(self):
        """Unisce Incaricato + Delega Diretta."""
        from app.deleghe_reader import fetch_all_deleghe_enhanced
        eng = self._make_engine_with_endpoint(
            "deleghe/ricevute",
            ["DIRECT01A01A000A", "DIRECT02A02A000B"],
        )
        log = []
        clienti, stat = fetch_all_deleghe_enhanced(
            engine=eng,
            logger_func=log.append,
        )
        assert len(clienti) >= 3  # 1 wizard + 2 dirette
        dirette = [d for d in clienti if d.get("tipo_delega") == "DELEGA_DIRETTA"]
        incaricati = [d for d in clienti if d.get("tipo_delega") == "INCARICATO"]
        assert len(dirette) == 2
        assert len(incaricati) == 1

    def test_only_incaricato(self):
        """Nessun endpoint funzionante → solo incaricato."""
        from app.deleghe_reader import fetch_all_deleghe_enhanced
        eng = self.MockEngine()
        log = []
        clienti, stat = fetch_all_deleghe_enhanced(
            engine=eng,
            logger_func=log.append,
        )
        assert len(clienti) == 1
        assert clienti[0]["tipo_delega"] == "INCARICATO"

    def test_dedup_same_cf(self):
        """Stesso CF in entrambi → prevale incaricato (primo)."""
        from app.deleghe_reader import fetch_all_deleghe_enhanced
        # Crea engine dove endpoint dirette restituisce lo stesso CF
        eng = self._make_engine_with_endpoint(
            "deleghe/ricevute",
            ["WIZARD01A01A000A"],  # stesso CF del wizard
        )
        log = []
        clienti, stat = fetch_all_deleghe_enhanced(
            engine=eng,
            logger_func=log.append,
        )
        assert len(clienti) == 1
        assert clienti[0]["tipo_delega"] == "INCARICATO"

    def test_no_engine_request_func(self):
        """Engine senza _request_with_x_appl → solleva ValueError."""
        from app.deleghe_reader import fetch_all_deleghe_enhanced
        class BadEngine:
            pass
        try:
            fetch_all_deleghe_enhanced(engine=BadEngine())
            assert False, "Dovrebbe sollevare ValueError"
        except ValueError:
            pass

    def test_fallback_csv(self):
        """Nessun endpoint trovato → usa CSV fallback."""
        from app.deleghe_reader import fetch_all_deleghe_enhanced
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write("CF;Denominazione\n")
            f.write("CSVDIR01A01A000A;CSV Delega\n")
            csv_path = f.name

        try:
            eng = self.MockEngine()
            log = []
            clienti, stat = fetch_all_deleghe_enhanced(
                engine=eng,
                logger_func=log.append,
                csv_path=csv_path,
            )
            assert len(clienti) == 2  # 1 wizard + 1 CSV
            dirette = [d for d in clienti if d.get("tipo_delega") == "DELEGA_DIRETTA"]
            assert len(dirette) == 1
            assert dirette[0]["cf"] == "CSVDIR01A01A000A"
        finally:
            os.unlink(csv_path)

    def test_stat_counts(self):
        """Verifica dict_stat con conteggi corretti."""
        from app.deleghe_reader import (
            fetch_all_deleghe_enhanced,
            TIPO_DELEGA_INCARICATO,
            TIPO_DELEGA_DIRETTA,
        )
        eng = self._make_engine_with_endpoint(
            "deleghe/elenco",
            ["DIR01", "DIR02", "DIR03"],
        )
        log = []
        clienti, stat = fetch_all_deleghe_enhanced(
            engine=eng,
            logger_func=log.append,
        )
        assert stat["totale"] == len(clienti)
        assert stat[TIPO_DELEGA_INCARICATO] >= 1
        assert stat[TIPO_DELEGA_DIRETTA] >= 3


class TestDebugProbeEndpoints:
    """Test per _debug_probe_endpoints()."""

    def test_no_endpoints_work(self):
        """Nessun endpoint funzionante → lista vuota."""
        from app.deleghe_reader import _debug_probe_endpoints

        def req_func(method, url):
            return MockResponse(404, "Not Found")

        session = None
        log = []
        result = _debug_probe_endpoints(req_func, session, log.append)
        assert result == []

    def test_direct_session_works(self):
        """Endpoint funziona via sessione diretta (senza x-appl)."""
        from app.deleghe_reader import (
            _debug_probe_endpoints, CANDIDATE_ENDPOINTS,
        )

        def req_func(method, url):
            return MockResponse(404, "Not Found")

        # La sessione risponde 200 per il PRIMO endpoint candidato
        # (gli endpoint sono ordinati per priorità decrescente)
        first_url = CANDIDATE_ENDPOINTS[0]["url"]

        class DirectSession:
            def request(self, method, url, **kwargs):
                if url == first_url:
                    # Restituisce almeno min_cf=2 CF per superare la soglia
                    return MockResponse(
                        200,
                        json_data=[
                            {"cf": "PROBE01", "denominazione": "Test SRL"},
                            {"cf": "PROBE02", "denominazione": "Test2 SRL"},
                        ],
                    )
                return MockResponse(404, "Not Found")

        log = []
        result = _debug_probe_endpoints(req_func, DirectSession(), log.append)
        assert len(result) == 2
        assert result[0]["cf"] == "PROBE01"
        assert result[1]["cf"] == "PROBE02"


class TestCandidateEndpoints:
    """Verifica che CANDIDATE_ENDPOINTS sia ben formato."""

    def test_all_are_dicts(self):
        from app.deleghe_reader import CANDIDATE_ENDPOINTS
        for ep in CANDIDATE_ENDPOINTS:
            assert isinstance(ep, dict), f"Endpoint non dict: {ep}"
            assert "method" in ep, f"Endpoint senza method: {ep}"
            assert "url" in ep, f"Endpoint senza url: {ep}"
            assert ep["method"] in ("GET", "POST")
            assert ep["url"].startswith("http")
            assert isinstance(ep.get("min_cf", 2), int)
            assert isinstance(ep.get("priority", 0), int)

    def test_min_15_endpoints(self):
        from app.deleghe_reader import CANDIDATE_ENDPOINTS
        assert len(CANDIDATE_ENDPOINTS) >= 15
