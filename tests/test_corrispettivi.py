"""
Test per app/corrispettivi_engine.py — corrispettivi telematici.
"""

import pytest

from app.corrispettivi_engine import (
    date_to_rest, parse_amount, _fmt, _extract_fields, TIPI_ELENCO,
)


class TestDateToRest:
    """Test per date_to_rest()."""

    @pytest.mark.parametrize("input_date, expected", [
        ("01/01/2025", "01012025"),
        ("31/12/2025", "31122025"),
        ("15/06/2024", "15062024"),
    ])
    def test_valid_dates(self, input_date, expected):
        assert date_to_rest(input_date) == expected

    def test_invalid_date_raises(self):
        """Data non valida deve sollevare eccezione."""
        with pytest.raises(ValueError):
            date_to_rest("13/13/2025")


class TestParseAmount:
    """Test per parse_amount()."""

    def test_none(self):
        assert parse_amount(None) is None

    def test_empty(self):
        assert parse_amount("") is None

    @pytest.mark.parametrize("input_val, expected", [
        ("1234,56", 1234.56),         # formato italiano
        ("1.234,56", 1234.56),        # con separatore migliaia
        ("1234.56", 1234.56),         # formato inglese
        ("+1.000,00", 1000.0),        # con segno +
        ("-500,00", -500.0),          # negativo
        ("1.000.000,00", 1000000.0),  # milioni
        ("0,00", 0.0),
        ("0.00", 0.0),
    ])
    def test_valid_amounts(self, input_val, expected):
        result = parse_amount(input_val)
        assert result == expected, f"parse_amount('{input_val}') = {result}, atteso {expected}"

    @pytest.mark.parametrize("input_val", [
        "N/A", "abc", "", ",,,",
    ])
    def test_invalid_returns_none(self, input_val):
        assert parse_amount(input_val) is None


class TestFmt:
    """Test per _fmt()."""

    def test_none(self):
        assert _fmt(None) == ""

    def test_float(self):
        assert _fmt(1234.5) == "1234.50"
        assert _fmt(0.0) == "0.00"
        assert _fmt(-500.0) == "-500.00"

    def test_int(self):
        assert _fmt(42) == "42"

    def test_string(self):
        assert _fmt("test") == "test"

    def test_zero(self):
        assert _fmt(0) == "0"


class TestTipiElenco:
    """Test per la costante TIPI_ELENCO."""

    def test_expected_types(self):
        assert "RT" in TIPI_ELENCO
        assert "DA" in TIPI_ELENCO
        assert "MC" in TIPI_ELENCO
        assert "CA" in TIPI_ELENCO
        assert len(TIPI_ELENCO) == 4


class TestExtractFields:
    """Test per _extract_fields()."""

    def test_empty_record(self):
        """Record vuoto deve restituire valori predefiniti."""
        result = _extract_fields({})
        assert result["id_invio"] == ""
        assert result["matricola"] == ""
        assert result["annullati"] == 0
        assert result["resi"] == 0
        assert result["imponibile"] is None
        assert result["imposta"] is None
        assert result["tipo_corrispettivo"] == ""

    def test_basic_record(self):
        """Record con dati basilari."""
        record = {
            "idInvio": "INV123",
            "matricolaDispositivo": "MAT456",
            "timeRilevazione": "2025-01-15T10:30:00",
            "importoParzialeTotale": "1000,00",
            "impostaTotale": "220,00",
        }
        result = _extract_fields(record)
        assert result["id_invio"] == "INV123"
        assert result["matricola"] == "MAT456"
        assert result["data_rilevazione"] == "2025-01-15T10:30:00"
        assert result["imponibile"] == 1000.0
        assert result["imposta"] == 220.0

    def test_annullati(self):
        """Campo annullati gestito."""
        record = {"annullati": "2"}
        result = _extract_fields(record)
        assert result["annullati"] == 2.0

    def test_aliquote_map(self):
        """Mappatura aliquote da riepilogo."""
        record = {
            "riepilogo": [
                {"aliquotaIva": 22, "imponibile": "1000,00"},
                {"aliquotaIva": 10, "imponibile": "500,00"},
            ]
        }
        result = _extract_fields(record)
        assert result["ali_22"] == 1000.0
        assert result["ali_10"] == 500.0
        assert result["ali_4"] is None


@pytest.mark.smoke
def test_corrispettivi_smoke():
    """Smoke test delle funzioni principali."""
    assert date_to_rest("01/01/2025") == "01012025"
    assert parse_amount("1.234,56") == 1234.56
    assert _fmt(1234.5) == "1234.50"
    print("Corrispettivi smoke test OK")
