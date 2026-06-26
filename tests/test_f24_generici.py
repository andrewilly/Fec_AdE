"""
Test per app/f24_generici_placeholder.py — download F24 generici dal Cassetto Fiscale.

NOTA: L'implementazione reale delega a CassettoFiscaleEngine.
I test mockano il motore per verificare il corretto wiring.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.f24_generici_placeholder import (
    run,
    probe_cassetto_fiscale_endpoints,
    TIPI_DOCUMENTO,
)


class TestRun:
    """Test per run()."""

    @patch("app.f24_generici_placeholder.CassettoFiscaleEngine")
    @patch("app.cassetto_fiscale_engine.run_f24_generici")
    def test_run_success(self, mock_run_f24, MockEngine):
        """Esecuzione con successo."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.init_session.return_value = {"esito": "OK"}
        mock_run_f24.return_value = True

        mock_session = MagicMock()
        success = run(
            mock_session, {}, "RSSMRA85M01H501Z", 2024, print,
            output_root="/tmp/_test", piva="01234567890"
        )

        assert success is True
        mock_engine_instance.init_session.assert_called_once()
        mock_run_f24.assert_called_once()

    @patch("app.f24_generici_placeholder.CassettoFiscaleEngine")
    def test_run_fail_init(self, MockEngine):
        """Fallimento init_session."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        from app.cassetto_fiscale_engine import CassettoFiscaleError
        mock_engine_instance.init_session.side_effect = CassettoFiscaleError(
            "Init fallito"
        )

        mock_session = MagicMock()
        with pytest.raises(CassettoFiscaleError, match="Init fallito"):
            run(mock_session, {}, "CF", 2024, print)

    @patch("app.f24_generici_placeholder.CassettoFiscaleEngine")
    @patch("app.cassetto_fiscale_engine.run_f24_generici")
    def test_run_uses_piva_from_param(self, mock_run_f24, MockEngine):
        """Usa piva dal parametro."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.init_session.return_value = {"esito": "OK"}
        mock_run_f24.return_value = True

        run(MagicMock(), {}, "CF", 2024, print, piva="01234567890")

        # Verifica che run_f24_generici riceva piva corretta
        _, kwargs = mock_run_f24.call_args
        assert kwargs.get("piva") == "01234567890"
        assert kwargs.get("cf") == "CF"

    @patch("app.f24_generici_placeholder.CassettoFiscaleEngine")
    @patch("app.cassetto_fiscale_engine.run_f24_generici")
    def test_run_fallback_to_cf(self, mock_run_f24, MockEngine):
        """Usa cf come piva se piva non specificato."""
        mock_engine_instance = MagicMock()
        MockEngine.return_value = mock_engine_instance
        mock_engine_instance.init_session.return_value = {"esito": "OK"}
        mock_run_f24.return_value = True

        run(MagicMock(), {}, "CF123", 2024, print)

        _, kwargs = mock_run_f24.call_args
        assert kwargs.get("piva") == "CF123"


class TestProbeEndpoints:
    """Test per probe_cassetto_fiscale_endpoints()."""

    def test_probe_success(self):
        """Probe endpoint funziona."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp

        result = probe_cassetto_fiscale_endpoints(mock_session, "CF", print)

        assert len(result) >= 5
        # Tutti gli endpoint devono essere stati testati
        for url, status in result.items():
            assert status in (200, 0), f"{url} ha status {status}"

    def test_probe_mixed_status(self):
        """Probe con status misti."""
        mock_session = MagicMock()

        responses = {
            "https://cassetto.agenziaentrate.gov.it/CassHomeWeb/home": 200,
            "https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initLight": 200,
            "https://cassetto.agenziaentrate.gov.it/casshome-rest/rs/initCassetto": 500,
        }

        def mock_get(url, **kwargs):
            for pattern, status in responses.items():
                if pattern in url:
                    return MagicMock(status_code=status)
            return MagicMock(status_code=404)

        mock_session.get.side_effect = mock_get

        result = probe_cassetto_fiscale_endpoints(mock_session, "CF", print)
        # Deve includere tutti gli endpoint testati
        assert len(result) >= 5


class TestTipoDocumento:
    """Test per TIPI_DOCUMENTO."""

    def test_contains_f24(self):
        assert "F24" in TIPI_DOCUMENTO
        assert "DetF24" in TIPI_DOCUMENTO


@pytest.mark.smoke
def test_f24_generici_smoke():
    """Smoke test."""
    assert "F24" in TIPI_DOCUMENTO
    print("F24 generici smoke test OK")
