"""
Test per app/engine.py — funzioni di utilità e classi.
"""

import pytest
from datetime import datetime

from app.engine import (
    unix_ms, add_months, get_date_chunks, FEScraperEngine,
)


class TestUnixMs:
    """Test per unix_ms()."""

    def test_returns_string(self):
        result = unix_ms()
        assert isinstance(result, str)

    def test_is_digits(self):
        result = unix_ms()
        assert result.isdigit()

    def test_reasonable_timestamp(self):
        """Il timestamp deve essere vicino all'epoca corrente (2026 = ~1.7B secondi)."""
        result = int(unix_ms())
        # 2026 → circa 1.8 miliardi di secondi → 1.8 trilioni di millisecondi
        assert 1_500_000_000_000 < result < 3_000_000_000_000


class TestAddMonths:
    """Test per add_months()."""

    def test_add_one_month(self):
        d = datetime(2025, 1, 15)
        result = add_months(d, 1)
        assert result.year == 2025
        assert result.month == 2
        assert result.day == 15

    def test_add_three_months(self):
        d = datetime(2025, 1, 1)
        result = add_months(d, 3)
        assert result.month == 4
        assert result.day == 1

    def test_year_crossing(self):
        d = datetime(2025, 11, 1)
        result = add_months(d, 3)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 1

    def test_jan31_plus_1_month(self):
        """31 gennaio + 1 mese = 28 febbraio (anno non bisestile)."""
        d = datetime(2025, 1, 31)
        result = add_months(d, 1)
        assert result.year == 2025
        assert result.month == 2
        assert result.day == 28

    def test_jan31_plus_1_month_leap_year(self):
        """31 gennaio + 1 mese in anno bisestile = 29 febbraio."""
        d = datetime(2024, 1, 31)
        result = add_months(d, 1)
        assert result.year == 2024
        assert result.month == 2
        assert result.day == 29

    def test_mar31_plus_1_month(self):
        """31 marzo + 1 mese = 30 aprile."""
        d = datetime(2025, 3, 31)
        result = add_months(d, 1)
        assert result.month == 4
        assert result.day == 30

    def test_dec_plus_2_months(self):
        """Dicembre + 2 mesi = febbraio dell'anno successivo."""
        d = datetime(2025, 12, 15)
        result = add_months(d, 2)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 15

    def test_large_months(self):
        """12 mesi = esattamente 1 anno dopo."""
        d = datetime(2025, 6, 15)
        result = add_months(d, 12)
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 15


class TestGetDateChunks:
    """Test per get_date_chunks()."""

    def test_single_chunk_short_period(self):
        """Periodo breve (< 3 mesi) → un solo chunk."""
        chunks = get_date_chunks("01/01/2025", "28/02/2025")
        assert len(chunks) == 1
        assert chunks[0] == ("01/01/2025", "28/02/2025")

    def test_three_months_exact(self):
        """Periodo esattamente di 3 mesi → un solo chunk."""
        chunks = get_date_chunks("01/01/2025", "31/03/2025")
        assert len(chunks) == 1

    def test_six_months_two_chunks(self):
        """Periodo di 6 mesi → 2 chunk."""
        chunks = get_date_chunks("01/01/2025", "30/06/2025")
        assert len(chunks) == 2
        assert chunks[0][0] == "01/01/2025"
        assert chunks[-1][1] == "30/06/2025"

    def test_full_year_four_chunks(self):
        """Un anno intero → circa 4 chunk."""
        chunks = get_date_chunks("01/01/2025", "31/12/2025")
        assert len(chunks) >= 3
        assert len(chunks) <= 5
        # Verifichia che i chunk coprano tutto l'intervallo
        assert chunks[0][0] == "01/01/2025"
        assert chunks[-1][1] == "31/12/2025"

    def test_chunks_contiguous(self):
        """I chunk devono essere contigui (fine di uno = inizio del successivo)."""
        chunks = get_date_chunks("01/01/2025", "31/12/2025")
        for i in range(len(chunks) - 1):
            from datetime import datetime as dt
            end_current = dt.strptime(chunks[i][1], "%d/%m/%Y")
            start_next = dt.strptime(chunks[i + 1][0], "%d/%m/%Y")
            assert end_current == start_next, (
                f"Gap tra chunk {i} e {i+1}: "
                f"{chunks[i][1]} → {chunks[i+1][0]}"
            )

    def test_no_chunk_if_start_equals_end(self):
        """Se start == end, nessun chunk (loop non eseguito)."""
        chunks = get_date_chunks("15/06/2025", "15/06/2025")
        assert len(chunks) == 0

    def test_date_formats(self):
        """Le date in output devono essere in formato DD/MM/YYYY."""
        chunks = get_date_chunks("01/01/2025", "30/06/2025")
        for start, end in chunks:
            # Prova a fare il parsing per verificare il formato
            datetime.strptime(start, "%d/%m/%Y")
            datetime.strptime(end, "%d/%m/%Y")


class TestFEScraperEngine:
    """Test per FEScraperEngine (metodi statici/di utilità)."""

    def test_engine_creation(self):
        """Creare un engine con una logger function non fallisce."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)
        assert engine.session is not None
        assert engine._x_appl is None
        assert engine._wizard_template == {}

    def test_session_user_agent(self):
        """La sessione deve avere un User-Agent Chrome."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)
        ua = engine.session.headers.get("User-Agent", "")
        assert "Chrome" in ua
        assert "Windows NT" in ua

    def test_safe_json_valid_dict(self):
        """safe_json restituisce dict validi."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        class MockResponse:
            def json(self):
                return {"key": "value"}

        result = engine._safe_json(MockResponse())
        assert result == {"key": "value"}

    def test_safe_json_invalid(self):
        """safe_json gestisce JSON non dict."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        class MockResponse:
            def json(self):
                return ["a", "b"]

        result = engine._safe_json(MockResponse())
        assert "_raw" in result

    def test_safe_json_exception(self):
        """safe_json gestisce eccezioni di parsing."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        class MockResponse:
            def json(self):
                raise ValueError("bad json")
            text = "not json"

        result = engine._safe_json(MockResponse())
        assert "_raw_text" in result

    def test_extract_piva_value_string(self):
        """_extract_piva_value estrae stringa dal campo 'pIva'."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        result = engine._extract_piva_value({"pIva": "01234567890"})
        assert result == "01234567890"

    def test_extract_piva_value_fallback(self):
        """_extract_piva_value usa fallback se 'pIva' è assente."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        result = engine._extract_piva_value({"CF": "ABC"}, fallback="FALLBACK")
        assert result == "FALLBACK"

    def test_extract_piva_value_PIva(self):
        """_extract_piva_value cerca anche 'PIva' (P maiuscola)."""
        def logger(msg):
            pass
        engine = FEScraperEngine(logger)

        result = engine._extract_piva_value({"PIva": "09876543210"})
        assert result == "09876543210"


@pytest.mark.smoke
def test_engine_smoke():
    """Smoke test: le funzioni principali rispondono."""
    ts = unix_ms()
    assert ts.isdigit()

    d = add_months(datetime(2025, 1, 31), 1)
    assert d.month == 2

    chunks = get_date_chunks("01/01/2025", "31/03/2025")
    assert len(chunks) == 1
    print(f"Engine smoke test OK — unix_ms={ts[:10]}..., chunks={len(chunks)}")
