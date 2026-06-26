"""
Test per app/cassetto_fiscale_engine.py — Cassetto Fiscale AE.
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.cassetto_fiscale_engine import (
    CassettoFiscaleEngine,
    CassettoFiscaleError,
    CassettoNotInitializedError,
    get_tipi_documento,
    TIPI_DOCUMENTO,
    TIPO_TO_RIC,
    export_json,
    CASSETTO_BASE,
    CASSETTO_HOME,
    CASSETTO_REST,
    CASSETTO_SERVLET,
)


class TestConstants:
    """Test per costanti del modulo."""

    def test_cassetto_base(self):
        assert CASSETTO_BASE == "https://cassetto.agenziaentrate.gov.it"

    def test_cassetto_home(self):
        assert CASSETTO_HOME.startswith(CASSETTO_BASE)

    def test_tipi_documento(self):
        assert "F24" in TIPI_DOCUMENTO
        assert "RED" in TIPI_DOCUMENTO
        assert "730" in TIPI_DOCUMENTO
        assert "770" in TIPI_DOCUMENTO
        assert "IVA" in TIPI_DOCUMENTO
        assert "UNI" in TIPI_DOCUMENTO

    def test_tipo_to_ric(self):
        assert TIPO_TO_RIC["redditi"] == "RED"
        assert TIPO_TO_RIC["730"] == "730"
        assert TIPO_TO_RIC["f24"] == "F24"
        assert TIPO_TO_RIC["iva"] == "IVA"

    def test_get_tipi_documento(self):
        tipi = get_tipi_documento()
        assert "F24" in tipi
        assert tipi["730"] == "Modello 730"


class TestCassettoFiscaleEngineInit:
    """Test per inizializzazione del CassettoFiscaleEngine."""

    def test_initial_state(self):
        """Engine appena creato non è inizializzato."""
        mock_session = MagicMock()
        engine = CassettoFiscaleEngine(mock_session, print)
        assert engine.initialized is False
        assert engine.chiave_cassetto is None

    def test_init_session_success(self):
        """init_session() chiama gli endpoint corretti."""
        mock_session = MagicMock()

        # Mock delle risposte HTTP
        home_resp = MagicMock()
        home_resp.status_code = 200

        init_light_resp = MagicMock()
        init_light_resp.status_code = 200

        init_cassetto_resp = MagicMock()
        init_cassetto_resp.status_code = 200
        init_cassetto_resp.json.return_value = {
            "chiaveCassetto": "CHIAVE_123",
            "esito": "OK"
        }

        # Home e initLight sono GET, initCassetto è POST
        mock_session.get.side_effect = [
            home_resp, init_light_resp,
        ]
        mock_session.post.return_value = init_cassetto_resp

        engine = CassettoFiscaleEngine(mock_session, print)
        result = engine.init_session()

        assert engine.initialized is True
        assert engine.chiave_cassetto == "CHIAVE_123"
        assert result["chiaveCassetto"] == "CHIAVE_123"

        # Verifica che initCassetto sia stato chiamato via POST
        assert mock_session.get.call_count == 2
        assert mock_session.post.call_count == 1
        mock_session.post.assert_called_once()
        # Verifica che l'URL contenga initCassetto
        call_url = mock_session.post.call_args[0][0]
        assert "initCassetto" in call_url

    def test_init_session_home_fail(self):
        """Fallimento home page."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_session.get.return_value = mock_resp

        engine = CassettoFiscaleEngine(mock_session, print)
        with pytest.raises(CassettoFiscaleError, match="Cassetto Fiscale non raggiungibile"):
            engine.init_session()

    def test_init_cassetto_fail(self):
        """Fallimento initCassetto."""
        mock_session = MagicMock()

        home_resp = MagicMock()
        home_resp.status_code = 200
        init_light_resp = MagicMock()
        init_light_resp.status_code = 200
        init_cassetto_resp = MagicMock()
        init_cassetto_resp.status_code = 403

        mock_session.get.side_effect = [
            home_resp, init_light_resp,
        ]
        mock_session.post.return_value = init_cassetto_resp

        engine = CassettoFiscaleEngine(mock_session, print)
        with pytest.raises(CassettoFiscaleError, match="Init Cassetto Fiscale"):
            engine.init_session()

    def test_init_session_network_error(self):
        """Errore di rete durante init."""
        mock_session = MagicMock()
        from requests import RequestException
        mock_session.get.side_effect = RequestException("Connection refused")

        engine = CassettoFiscaleEngine(mock_session, print)
        with pytest.raises(CassettoFiscaleError, match="Connessione"):
            engine.init_session()


class TestCambiaCliente:
    """Test per cambio cliente."""

    def test_cambia_cliente_not_initialized(self):
        """cambia_cliente() senza init_session() fallisce."""
        engine = CassettoFiscaleEngine(MagicMock(), print)
        with pytest.raises(CassettoNotInitializedError):
            engine.cambia_cliente("01234567890")

    def test_cambia_cliente_success(self):
        """Cambio cliente riuscito con pIva."""
        mock_session = MagicMock()

        home_resp = MagicMock()
        home_resp.status_code = 200
        init_light_resp = MagicMock()
        init_light_resp.status_code = 200
        init_cassetto_resp = MagicMock()
        init_cassetto_resp.status_code = 200
        init_cassetto_resp.json.return_value = {"esito": "OK"}

        cambia_resp = MagicMock()
        cambia_resp.status_code = 200
        cambia_resp.json.return_value = {"esito": "OK", "cliente": "01234567890"}

        # GET: home, initLight
        mock_session.get.side_effect = [
            home_resp, init_light_resp,
        ]
        # POST: initCassetto (OK), poi cambiaCliente (OK)
        mock_session.post.side_effect = [
            init_cassetto_resp, cambia_resp,
        ]

        engine = CassettoFiscaleEngine(mock_session, print)
        engine.init_session()
        result = engine.cambia_cliente("01234567890")

        assert engine._current_piva == "01234567890"
        assert result.get("esito") == "OK"
        # POST: initCassetto + cambiaCliente(1 tentativo) = 2
        assert mock_session.post.call_count == 2

    def test_cambia_cliente_fallisce_piva_poi_cf(self):
        """409 su pIva → riprova con cf."""
        mock_session = MagicMock()

        home_resp = MagicMock()
        home_resp.status_code = 200
        init_light_resp = MagicMock()
        init_light_resp.status_code = 200
        init_cassetto_resp = MagicMock()
        init_cassetto_resp.status_code = 200
        init_cassetto_resp.json.return_value = {"esito": "OK"}

        # Prima risposta: 409 con pIva
        resp_409 = MagicMock()
        resp_409.status_code = 409
        resp_409.text = '{"message":"Conflitto"}'
        resp_409.json.return_value = {"message": "Conflitto"}

        # Seconda risposta: 200 con cf
        resp_cf_ok = MagicMock()
        resp_cf_ok.status_code = 200
        resp_cf_ok.json.return_value = {"esito": "OK", "cliente": "RZZNGL83E46L628K"}

        # GET: home, initLight
        mock_session.get.side_effect = [
            home_resp, init_light_resp,
        ]
        # POST: initCassetto (OK), pIva (409), cf (OK) = 3
        mock_session.post.side_effect = [
            init_cassetto_resp, resp_409, resp_cf_ok,
        ]

        engine = CassettoFiscaleEngine(mock_session, print)
        engine.init_session()
        result = engine.cambia_cliente("RZZNGL83E46L628K")

        assert engine._current_piva == "RZZNGL83E46L628K"
        assert result.get("esito") == "OK"
        assert mock_session.post.call_count == 3  # initCassetto + pIva + cf

    def test_cambia_cliente_entrambi_falliscono(self):
        """Sia pIva che cf danno 409 → errore."""
        mock_session = MagicMock()

        home_resp = MagicMock()
        home_resp.status_code = 200
        init_light_resp = MagicMock()
        init_light_resp.status_code = 200
        init_cassetto_resp = MagicMock()
        init_cassetto_resp.status_code = 200
        init_cassetto_resp.json.return_value = {"esito": "OK"}

        resp_409 = MagicMock()
        resp_409.status_code = 409
        resp_409.text = '{"message":"Conflitto"}'
        resp_409.json.return_value = {"message": "Conflitto"}

        # GET: home, initLight
        mock_session.get.side_effect = [
            home_resp, init_light_resp,
        ]
        # POST: initCassetto, pIva(409), cf(409) = 3
        mock_session.post.side_effect = [
            init_cassetto_resp, resp_409, resp_409,
        ]

        engine = CassettoFiscaleEngine(mock_session, print)
        engine.init_session()
        with pytest.raises(CassettoFiscaleError, match="Cambio cliente"):
            engine.cambia_cliente("RZZNGL83E46L628K")


class TestNavigazione:
    """Test per navigazione servlet."""

    def test_navigate_not_initialized(self):
        """navigate_servlet() senza init_session() fallisce."""
        engine = CassettoFiscaleEngine(MagicMock(), print)
        with pytest.raises(CassettoNotInitializedError):
            engine.navigate_servlet("F24", 2024)

    def test_navigate_with_cambio_cliente(self):
        """Naviga con cambio cliente automatico."""
        mock_session = MagicMock()

        # init_session (home e initLight sono GET, initCassetto è POST)
        mock_session.get.side_effect = [
            MagicMock(status_code=200),  # home
            MagicMock(status_code=200),  # initLight
        ]
        mock_session.post.return_value = MagicMock(
            status_code=200, json=lambda: {"esito": "OK"}  # initCassetto
        )

        # cambia_cliente
        mock_session.post.return_value = MagicMock(
            status_code=200, json=lambda: {"esito": "OK"}
        )

        # servlet
        servlet_resp = MagicMock()
        servlet_resp.status_code = 200
        servlet_resp.text = "<html><table>...</table></html>"

        # Dopo init_session, il side_effect per navigate_servlet
        mock_session.get.side_effect = None
        mock_session.get.return_value = servlet_resp

        engine = CassettoFiscaleEngine(mock_session, print)
        engine._initialized = True  # Simula init già fatto

        status, html = engine.navigate_servlet("F24", 2024, piva="01234567890")
        assert status == 200
        assert "<table>" in html

    def test_parse_table_html(self):
        """Parsing tabella HTML."""
        html_text = """
        <html>
        <body>
        <table class="elenco">
            <tr>
                <th>Data</th>
                <th>Importo</th>
                <th>Stato</th>
            </tr>
            <tr>
                <td>15/03/2024</td>
                <td>1.250,00</td>
                <td>Pagato</td>
            </tr>
            <tr>
                <td>20/06/2024</td>
                <td>850,50</td>
                <td>In elaborazione</td>
            </tr>
        </table>
        </body>
        </html>
        """
        engine = CassettoFiscaleEngine(MagicMock(), print)
        records = engine.parse_table_from_html(html_text)

        assert len(records) == 2
        assert records[0]["Data"] == "15/03/2024"
        assert records[0]["Importo"] == "1.250,00"
        assert records[1]["Stato"] == "In elaborazione"

    def test_parse_table_empty(self):
        """HTML senza tabelle."""
        html_text = "<html><body>Nessun dato disponibile</body></html>"
        engine = CassettoFiscaleEngine(MagicMock(), print)
        records = engine.parse_table_from_html(html_text)
        assert records == []

    def test_parse_table_with_links(self):
        """Tabella con link ai documenti."""
        html_text = """
        <html>
        <body>
        <table>
            <tr><th>Documento</th><th>Data</th></tr>
            <tr>
                <td><a href="/cassfisc-web/download?id=123">F24_2024.pdf</a></td>
                <td>15/03/2024</td>
            </tr>
        </table>
        </body>
        </html>
        """
        engine = CassettoFiscaleEngine(MagicMock(), print)
        records = engine.parse_table_from_html(html_text)

        assert len(records) == 1
        # Il link deve essere convertito in URL assoluto
        assert CASSETTO_BASE in (records[0].get("url") or "")
        assert records[0].get("Documento") == "F24_2024.pdf"

    def test_parse_document_links(self):
        """Estrazione link a documenti."""
        html_text = """
        <html>
        <body>
        <a href="/cassfisc-web/download/f24?anno=2024">F24 2024</a>
        <a href="https://cassetto.agenziaentrate.gov.it/cassfisc-web/doc/730_2023.pdf">730</a>
        </body>
        </html>
        """
        engine = CassettoFiscaleEngine(MagicMock(), print)
        docs = engine.parse_document_links_from_html(html_text)

        assert len(docs) >= 1
        # Almeno un link PDF o download
        found_download = any(
            d["tipo"] in ("DOWNLOAD", "PDF", "PAGINA")
            for d in docs
        )
        assert found_download


class TestImportExport:
    """Test per funzioni di salvataggio."""

    def test_export_json(self, tmpdir):
        """Esportazione JSON."""
        records = [
            {"data": "15/03/2024", "importo": "1250,00"},
            {"data": "20/06/2024", "importo": "850,50"},
        ]
        path = export_json(records, "RSSMRA85M01H501Z", 2024, "F24_GENERICI", str(tmpdir))
        import os
        assert os.path.exists(path)
        import json
        with open(path, "r") as f:
            data = json.load(f)
        assert data["totale"] == 2
        assert data["categoria"] == "F24_GENERICI"
        assert data["cf"] == "RSSMRA85M01H501Z"


@pytest.mark.smoke
def test_cassetto_fiscale_smoke():
    """Smoke test delle funzioni principali."""
    assert CASSETTO_BASE == "https://cassetto.agenziaentrate.gov.it"
    assert len(TIPI_DOCUMENTO) >= 10
    assert TIPO_TO_RIC["redditi"] == "RED"
    assert TIPO_TO_RIC["f24 generici"] == "F24"
    print("Cassetto Fiscale smoke test OK")
