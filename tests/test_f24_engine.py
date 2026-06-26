"""
Test per app/f24_engine.py — download F24 BOLLI (imposta di bollo).
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.f24_engine import (
    fetch_bollo_list,
    fetch_bollo_dettaglio,
    fetch_bollo_pdf,
    fetch_bolli_for_year,
    export_json,
    save_pdf,
    run,
    BASE_URL,
    BOLLO_LIST_ENDPOINT,
    BOLLO_PRINT_ENDPOINT,
)


# ─── Mock helpers ───────────────────────────────────────────────────────────────


def _mock_response(status_code=200, json_data=None, content=b"", headers=None):
    m = MagicMock()
    m.status_code = status_code
    if json_data is not None:
        m.json.return_value = json_data
    else:
        m.json.side_effect = ValueError("No JSON")
    m.content = content
    m.headers = headers or {}
    return m


# ─── Test fetch_bollo_list ──────────────────────────────────────────────────────


class TestFetchBolloList:
    def test_successful_response(self):
        """Risposta 200 con elenco valido."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            json_data={
                "fattureBollo": [
                    {
                        "progressivoBollo": "1",
                        "codiceFiscale": "RSSMRA85M01H501Z",
                        "partitaIva": "01234567890",
                        "imponibile": "5000.00",
                        "imposta": "10.00",
                        "numeroFatture": 5,
                    },
                    {
                        "progressivoBollo": "2",
                        "codiceFiscale": "RSSMRA85M01H501Z",
                        "partitaIva": "01234567890",
                        "imponibile": "3000.00",
                        "imposta": "6.00",
                        "numeroFatture": 3,
                    },
                ]
            }
        )

        logs = []
        result = fetch_bollo_list(
            mock_session, {"x-token": "abc"}, "01234567890", 2024, 1, logs.append
        )

        assert len(result) == 2
        assert result[0]["progressivoBollo"] == "1"
        assert result[1]["imponibile"] == "3000.00"
        assert any("trovati 2 elementi" in msg for msg in logs)

    def test_http_error(self):
        """HTTP non 200 restituisce lista vuota."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(status_code=404)

        logs = []
        result = fetch_bollo_list(
            mock_session, {}, "01234567890", 2024, 1, logs.append
        )
        assert result == []

    def test_network_error(self):
        """Errore di rete restituisce lista vuota."""
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Timeout")

        logs = []
        result = fetch_bollo_list(
            mock_session, {}, "01234567890", 2024, 1, logs.append
        )
        assert result == []

    def test_invalid_json(self):
        """JSON non valido restituisce lista vuota."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            status_code=200, json_data=None
        )

        logs = []
        result = fetch_bollo_list(
            mock_session, {}, "01234567890", 2024, 1, logs.append
        )
        assert result == []

    def test_empty_list(self):
        """Lista vuota."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            json_data={"fattureBollo": []}
        )

        logs = []
        result = fetch_bollo_list(
            mock_session, {}, "01234567890", 2024, 1, logs.append
        )
        assert result == []

    def test_invalid_trimestre(self):
        """Trimestre fuori range restituisce lista vuota."""
        mock_session = MagicMock()
        logs = []
        result = fetch_bollo_list(
            mock_session, {}, "01234567890", 2024, 5, logs.append
        )
        assert result == []
        assert any("non valido" in msg for msg in logs)


# ─── Test fetch_bollo_dettaglio ────────────────────────────────────────────────


class TestFetchBolloDettaglio:
    def test_successful_detail(self):
        """Dettaglio bollo restituito correttamente."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            json_data={
                "progressivoBollo": "1",
                "codiceFiscale": "RSSMRA85M01H501Z",
                "imponibile": "5000.00",
                "imposta": "10.00",
                "numeroFatture": 5,
                "dettaglioFatture": [
                    {"idFattura": "ABC123", "imponibile": "1000.00"}
                ],
            }
        )

        logs = []
        result = fetch_bollo_dettaglio(
            mock_session, {}, "01234567890", 2024, 1, logs.append
        )
        assert result is not None
        assert result["numeroFatture"] == 5
        assert len(result["dettaglioFatture"]) == 1

    def test_not_found(self):
        """Dettaglio non trovato."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(status_code=404)

        result = fetch_bollo_dettaglio(
            mock_session, {}, "01234567890", 2024, 1, print
        )
        assert result is None

    def test_network_error(self):
        """Errore di rete."""
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection error")

        result = fetch_bollo_dettaglio(
            mock_session, {}, "01234567890", 2024, 1, print
        )
        assert result is None


# ─── Test fetch_bollo_pdf ──────────────────────────────────────────────────────


class TestFetchBolloPdf:
    def test_successful_pdf(self):
        """PDF generato correttamente."""
        mock_session = MagicMock()
        pdf_content = b"%PDF-1.4 fake pdf content..."
        mock_session.post.return_value = _mock_response(
            content=pdf_content,
            headers={"Content-Type": "application/pdf"},
        )

        bollo_data = {
            "progressivoBollo": "1",
            "codiceFiscale": "RSSMRA85M01H501Z",
            "imponibile": "5000.00",
        }

        logs = []
        result = fetch_bollo_pdf(mock_session, {"x-token": "abc"}, bollo_data, logs.append)
        assert result == pdf_content

    def test_http_error(self):
        """HTTP non 200."""
        mock_session = MagicMock()
        mock_session.post.return_value = _mock_response(status_code=500)

        result = fetch_bollo_pdf(mock_session, {}, {}, print)
        assert result is None

    def test_network_error(self):
        """Errore di rete."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("Connection error")

        result = fetch_bollo_pdf(mock_session, {}, {}, print)
        assert result is None


# ─── Test fetch_bolli_for_year ────────────────────────────────────────────────


class TestFetchBolliForYear:
    def test_all_trimesters_with_pdf(self):
        """Recupero bolli per tutti i trimestri con PDF."""
        mock_session = MagicMock()

        # Mock per ogni trimestre: risposte alterne
        def mock_get_side_effect(url, **kwargs):
            if "elenco" in url:
                if "2024/2" in url:
                    return _mock_response(json_data={"fattureBollo": []})
                return _mock_response(
                    json_data={
                        "fattureBollo": [
                            {
                                "progressivoBollo": "1",
                                "codiceFiscale": "RSSMRA85M01H501Z",
                                "imponibile": "5000.00",
                            }
                        ]
                    }
                )
            return _mock_response(status_code=404)

        mock_session.get.side_effect = mock_get_side_effect
        mock_session.post.return_value = _mock_response(
            content=b"%PDF bollo",
            headers={"Content-Type": "application/pdf"},
        )

        logs = []
        result = fetch_bolli_for_year(
            mock_session, {}, "01234567890", 2024, logs.append, download_pdf=True
        )

        # Dovremmo avere risultati per T1, T3, T4 (T2 vuoto)
        assert 1 in result
        assert 2 not in result  # vuoto
        assert 3 in result
        assert 4 in result
        assert any("pdf_bytes" in rec for rec in result[1])

    def test_no_bolli(self):
        """Nessun bollo per l'anno."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            json_data={"fattureBollo": []}
        )

        logs = []
        result = fetch_bolli_for_year(
            mock_session, {}, "01234567890", 2024, logs.append, download_pdf=False
        )
        assert result == {}


# ─── Test export_json ──────────────────────────────────────────────────────────


class TestExportJson:
    def test_export_without_pdf_bytes(self):
        """Esportazione JSON senza includere i bytes PDF."""
        records_by_t = {
            1: [
                {
                    "piva": "01234567890",
                    "anno": 2024,
                    "trimestre": 1,
                    "progressivoBollo": "1",
                    "imponibile": "5000.00",
                    "pdf_bytes": b"%PDF fake",
                },
            ],
            3: [
                {
                    "piva": "01234567890",
                    "anno": 2024,
                    "trimestre": 3,
                    "progressivoBollo": "2",
                    "imponibile": "3000.00",
                    "pdf_bytes": b"%PDF fake 2",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_json(records_by_t, "01234567890", 2024, tmpdir)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["totale"] == 2
            assert data["piva"] == "01234567890"
            assert len(data["bolli"]) == 2
            # Verifica che pdf_bytes NON sia presente
            for bollo in data["bolli"]:
                assert "pdf_bytes" not in bollo


# ─── Test save_pdf ─────────────────────────────────────────────────────────────


class TestSavePdf:
    def test_save(self):
        """Salvataggio PDF."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_pdf(b"%PDF test", "01234567890", 2024, 1, tmpdir)
            assert os.path.exists(path)
            assert path.endswith(".pdf")
            assert "F24_BOLLO" in path
            assert "T1" in path
            with open(path, "rb") as f:
                assert f.read() == b"%PDF test"

    def test_save_with_suffix(self):
        """Salvataggio PDF con suffisso."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_pdf(b"%PDF test", "01234567890", 2024, 2, tmpdir, suffix="extra")
            assert os.path.exists(path)
            assert "extra" in path


# ─── Test run ──────────────────────────────────────────────────────────────────


class TestRun:
    def test_run_no_records(self):
        """Esecuzione senza risultati non fallisce."""
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            json_data={"fattureBollo": []}
        )

        success = run(
            mock_session, {}, "01234567890", 2024, print,
            output_root="/tmp/_f24_test",
        )
        assert success is True

    def test_run_with_records(self):
        """Esecuzione con record."""
        mock_session = MagicMock()

        def mock_get_side_effect(url, **kwargs):
            return _mock_response(
                json_data={
                    "fattureBollo": [
                        {
                            "progressivoBollo": "1",
                            "codiceFiscale": "RSSMRA85M01H501Z",
                            "imponibile": "5000.00",
                        }
                    ]
                }
            )

        mock_session.get.side_effect = mock_get_side_effect
        mock_session.post.return_value = _mock_response(
            content=b"%PDF bollo",
            headers={"Content-Type": "application/pdf"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            success = run(
                mock_session, {}, "01234567890", 2024, print,
                output_root=tmpdir,
                fmt="both",
                download_pdf=True,
            )
            assert success is True


# ─── Smoke test ────────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_f24_engine_smoke():
    """Smoke test delle funzioni principali (costanti)."""
    assert BASE_URL == "https://ivaservizi.agenziaentrate.gov.it/cons/cons-services/rs"
    assert "fe/bollo/elenco" in BOLLO_LIST_ENDPOINT
    assert "fe/bollo/stampa/F24" in BOLLO_PRINT_ENDPOINT
    print("F24 bolli engine smoke test OK")
