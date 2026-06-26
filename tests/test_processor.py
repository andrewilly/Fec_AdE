"""
Test per app/processor.py — parsing XML e utility.
"""

import pytest

from app.processor import (
    clean_float, get_text, _infer_transfrontaliera,
)


class TestCleanFloat:
    """Test per clean_float()."""

    def test_none(self):
        assert clean_float(None) == 0.0

    def test_empty_string(self):
        assert clean_float("") == 0.0

    @pytest.mark.parametrize("input_val, expected", [
        ("1234,56", 1234.56),       # italiano semplice
        ("1.234,56", 1234.56),      # italiano con separatore migliaia
        ("1234.56", 1234.56),       # formato inglese
        ("0,00", 0.0),              # zero italiano
        ("0.00", 0.0),              # zero inglese
        ("1.000.000,00", 1000000.0),  # milioni italiano
        ("1000000.00", 1000000.0),  # milioni inglese
        ("-500,00", -500.0),        # negativo italiano
        ("-500.00", -500.0),        # negativo inglese
        ("+200,50", 200.5),         # positivo esplicito
    ])
    def test_valid_inputs(self, input_val, expected):
        assert clean_float(input_val) == expected

    def test_invalid_string(self):
        """Stringa non numerica → 0.0."""
        assert clean_float("N/A") == 0.0
        assert clean_float("abc") == 0.0
        assert clean_float(",,,") == 0.0


class TestInferTransfrontaliera:
    """Test per _infer_transfrontaliera()."""

    def test_nazionale_ricevute(self):
        path = "output/01234567890_FE/RICEVUTE/FATTURE/IT123.xml"
        assert _infer_transfrontaliera(path) == 0

    def test_nazionale_emesse(self):
        path = "output/01234567890_FE/EMESSE/FATTURE/IT123.xml"
        assert _infer_transfrontaliera(path) == 0

    def test_emessa_transfrontaliera(self):
        path = "output/01234567890_FE/EMESSE_TRANSFRONTALIERE/FATTURE/FR123.xml"
        assert _infer_transfrontaliera(path) == 1

    def test_ricevuta_transfrontaliera(self):
        path = "output/01234567890_FE/RICEVUTE_TRANSFRONTALIERE/FATTURE/DE123.xml"
        assert _infer_transfrontaliera(path) == 2

    def test_windows_path_ricevute(self):
        """Path in formato Windows (backslash)."""
        path = "output\\01234567890_FE\\RICEVUTE\\FATTURE\\IT123.xml"
        # La funzione usa os.path.normpath() ora, che funziona cross-platform
        result = _infer_transfrontaliera(path)
        assert result in (0, 2)  # RICEVUTE = 0, RICEVUTE_TRANSFRONTALIERE = 2

    def test_windows_path_transfrontaliera(self):
        path = "output\\01234567890_FE\\EMESSE_TRANSFRONTALIERE\\FATTURE\\FR123.xml"
        result = _infer_transfrontaliera(path)
        assert result == 1


class TestXmlParsing:
    """Test per funzioni di parsing XML (senza file XML reali)."""

    def test_get_text_element_exists(self):
        """get_text deve restituire il testo di un elemento presente."""
        from lxml import etree
        xml = '<root><child xmlns:ns="http://test">CIAO</child></root>'
        root = etree.fromstring(xml.encode())
        result = get_text(root, ".//*[local-name()='child']")
        assert result == "CIAO"

    def test_get_text_element_missing(self):
        """get_text deve restituire None per elemento assente."""
        from lxml import etree
        xml = '<root><child>valore</child></root>'
        root = etree.fromstring(xml.encode())
        result = get_text(root, ".//*[local-name()='missing']")
        assert result is None

    def test_get_text_element_empty(self):
        """get_text restituisce None per elemento vuoto."""
        from lxml import etree
        xml = '<root><child></child></root>'
        root = etree.fromstring(xml.encode())
        result = get_text(root, ".//*[local-name()='child']")
        assert result is None


@pytest.mark.smoke
def test_processor_smoke():
    """Smoke test delle funzioni principali."""
    assert clean_float("1234,56") == 1234.56
    assert clean_float(None) == 0.0
    assert _infer_transfrontaliera("test/EMESSE_TRANSFRONTALIERE/test.xml") == 1
    print("Processor smoke test OK")
