"""
Test per app/dichiarazioni_engine.py — download dichiarazioni dal Cassetto Fiscale.

NOTA: La nuova implementazione usa il Cassetto Fiscale
(cassetto.agenziaentrate.gov.it) invece delle vecchie API ipotizzate su ivaservizi.
I test mockano la CassettoFiscaleEngine.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.dichiarazioni_engine import (
    fetch_dichiarazioni_list,
    fetch_dichiarazione_pdf,
    export_json,
    save_pdf,
    run,
    get_tipi_dichiarazione,
    TIPI_DICHIARAZIONE,
)


class TestFetchDichiarazioniList:
    """Test per fetch_dichiarazioni_list()."""

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_successful_response(self, MockEngine):
        """Risposta 200 con elenco valido."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance

        mock_engine_instance.fetch_dichiarazioni_list.return_value = [
            {
                "data": "15/06/2024",
                "descrizione": "Modello Redditi 2024",
                "url": "https://cassetto.agenziaentrate.gov.it/download/123",
            },
            {
                "data": "10/07/2024",
                "descrizione": "Modello Redditi 2024 (integrativa)",
                "url": "https://cassetto.agenziaentrate.gov.it/download/456",
            },
        ]
        # init_session fa il mock dei 3 step (home, initLight, initCassetto)
        mock_engine_instance.init_session.return_value = {"esito": "OK"}

        mock_session = MagicMock()
        logs = []

        result = fetch_dichiarazioni_list(
            mock_session, {"x-token": "abc"}, "RSSMRA85M01H501Z",
            2024, logs.append, tipo="RED"
        )

        assert len(result) == 2
        # Verifica che sia stato normalizzato con i campi id, tipo, etc.
        assert result[0].get("tipo") == "RED"
        assert result[0].get("tipo_label") == "Modello Redditi"
        assert result[0].get("id") != ""
        assert result[0].get("identificativo") == "Modello Redditi 2024"
        assert result[1].get("data") == "10/07/2024"

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_empty_list(self, MockEngine):
        """Nessun risultato."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.fetch_dichiarazioni_list.return_value = []
        mock_engine_instance.init_session.return_value = {"esito": "OK"}

        result = fetch_dichiarazioni_list(
            MagicMock(), {}, "CF", 2024, print
        )
        assert result == []

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_engine_error(self, MockEngine):
        """Errore del motore Cassetto Fiscale."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.fetch_dichiarazioni_list.side_effect = Exception(
            "Servizio non disponibile"
        )
        mock_engine_instance.init_session.return_value = {"esito": "OK"}

        result = fetch_dichiarazioni_list(
            MagicMock(), {}, "CF", 2024, print, tipo="730"
        )
        assert result == []

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_init_session_fail(self, MockEngine):
        """init_session fallisce (eccezione propagata)."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        from app.cassetto_fiscale_engine import CassettoFiscaleError
        mock_engine_instance.init_session.side_effect = CassettoFiscaleError(
            "Accesso negato"
        )

        # La funzione lascia propagare l'eccezione di init_session
        with pytest.raises(CassettoFiscaleError, match="Accesso negato"):
            fetch_dichiarazioni_list(
                MagicMock(), {}, "CF", 2024, print, tipo="RED"
            )


class TestFetchDichiarazionePdf:
    """Test per fetch_dichiarazione_pdf()."""

    def test_successful_pdf(self):
        """PDF scaricato correttamente."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"%PDF-1.4 test pdf content"
        mock_session.get.return_value = mock_response

        url = "https://cassetto.agenziaentrate.gov.it/download/doc.pdf"
        result = fetch_dichiarazione_pdf(
            mock_session, {}, "CF", 2024, url, print
        )
        assert result == b"%PDF-1.4 test pdf content"

    def test_not_found(self):
        """PDF non trovato."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        url = "https://cassetto.agenziaentrate.gov.it/download/missing.pdf"
        result = fetch_dichiarazione_pdf(
            mock_session, {}, "CF", 2024, url, print
        )
        assert result is None

    def test_network_error(self):
        """Errore di rete."""
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Network error")

        result = fetch_dichiarazione_pdf(
            mock_session, {}, "CF", 2024,
            "https://cassetto.agenziaentrate.gov.it/download/doc.pdf",
            print
        )
        assert result is None

    def test_non_url_identificativo(self):
        """Identificativo non URL (deprecato) restituisce None."""
        mock_session = MagicMock()
        result = fetch_dichiarazione_pdf(
            mock_session, {}, "CF", 2024, "DCH_001", print
        )
        assert result is None


class TestExportJson:
    """Test per export_json()."""

    def test_export(self):
        """Esportazione JSON dei metadati."""
        records = [
            {"id": "DCH_001", "tipo": "RED"},
            {"id": "DCH_002", "tipo": "730"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_json(records, "RSSMRA85M01H501Z", 2024, tmpdir)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["totale"] == 2
            assert data["cf"] == "RSSMRA85M01H501Z"
            assert len(data["documenti"]) == 2


class TestSavePdf:
    """Test per save_pdf()."""

    def test_save(self):
        """Salvataggio file PDF."""
        content = b"%PDF-1.4 test"
        with tempfile.TemporaryDirectory() as tmpdir:
            # Usa un identificativo URL-like
            path = save_pdf(
                content, "CF", 2024,
                "https://cassetto.agenziaentrate.gov.it/download/doc.pdf",
                tmpdir
            )
            assert os.path.exists(path)
            assert path.endswith(".pdf")
            with open(path, "rb") as f:
                assert f.read() == content

    def test_save_short_name(self):
        """Salvataggio con identificativo corto."""
        content = b"data"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_pdf(content, "CF", 2024, "doc_123", tmpdir)
            assert os.path.exists(path)
            assert path.endswith(".pdf")


class TestRun:
    """Test per run()."""

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_run_success(self, MockEngine):
        """Esecuzione con successo."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.init_session.return_value = {"esito": "OK"}

        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=200)

        # Mock di run_dichiarazioni (importato da cassetto_fiscale_engine)
        with patch("app.cassetto_fiscale_engine.run_dichiarazioni") as mock_run:
            mock_run.return_value = True
            success = run(
                mock_session, {}, "CF", 2024, print,
                output_root="/tmp/_dich_test"
            )
            assert success is True

    @patch("app.dichiarazioni_engine.CassettoFiscaleEngine")
    def test_run_fail(self, MockEngine):
        """Esecuzione fallita."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.init_session.return_value = {"esito": "OK"}

        with patch("app.cassetto_fiscale_engine.run_dichiarazioni") as mock_run:
            mock_run.return_value = False
            success = run(
                MagicMock(), {}, "CF", 2024, print,
                output_root="/tmp/_dich_test"
            )
            assert success is False


class TestGetTipiDichiarazione:
    """Test per get_tipi_dichiarazione()."""

    def test_contains_expected_types(self):
        tipi = get_tipi_dichiarazione()
        # I nuovi codici Ric
        assert "RED" in tipi
        assert "730" in tipi
        assert "IVA" in tipi
        assert "770" in tipi
        assert "UNI" in tipi
        assert tipi["730"] == "Modello 730"
        # I vecchi codici non dovrebbero più esserci
        assert "REDDITI_PF" not in tipi

    def test_tipi_constant(self):
        assert "RED" in TIPI_DICHIARAZIONE
        assert "730" in TIPI_DICHIARAZIONE
        assert "IVA" in TIPI_DICHIARAZIONE


@pytest.mark.smoke
def test_dichiarazioni_engine_smoke():
    """Smoke test delle funzioni principali."""
    tipi = get_tipi_dichiarazione()
    assert len(tipi) >= 5
    assert TIPI_DICHIARAZIONE["RED"] == "Modello Redditi"
    print("Dichiarazioni engine smoke test OK")
