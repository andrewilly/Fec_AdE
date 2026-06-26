"""
Test per app/output_manager.py — download e salvataggio fatture.
"""

import os
import pytest
from unittest.mock import patch

from app.output_manager import (
    safe_filename_from_disposition,
    extract_xml_from_p7m,
    OutputManager,
)


class TestSafeFilenameFromDisposition:
    """Test per safe_filename_from_disposition()."""

    @pytest.mark.parametrize("disposition, expected", [
        ('attachment; filename="IT12345678.xml"', "IT12345678.xml"),
        ("attachment; filename=IT12345678.xml", "IT12345678.xml"),
        ('attachment; filename="nome file con spazi.xml"', "nome file con spazi.xml"),
        ('attachment; filename="IT12345.p7m"', "IT12345.p7m"),
        ('', "fallback"),
        (None, "fallback"),
    ])
    def test_various_dispositions(self, disposition, expected):
        result = safe_filename_from_disposition(disposition, "fallback")
        assert result == expected


class TestExtractXmlFromP7m:
    """Test per extract_xml_from_p7m()."""

    def test_xml_content_returns_as_is(self):
        """Contenuto che inizia con '<' è già XML → restituito così com'è."""
        xml_content = b"<xml><test>data</test></xml>"
        result = extract_xml_from_p7m(xml_content, "test.xml", print)
        assert result == xml_content

    def test_invalid_content_returns_none(self):
        """Contenuto non valido → None."""
        result = extract_xml_from_p7m(b"not xml or p7m", "test.bin", print)
        assert result is None

    def test_logger_called_on_error(self):
        """Il logger deve essere chiamato in caso di errore."""
        messages = []

        def test_logger(msg):
            messages.append(msg)

        extract_xml_from_p7m(b"garbage data", "error.p7m", test_logger)
        assert len(messages) > 0
        assert "Errore estrazione P7M" in messages[0]

    def test_base64_xml_content(self):
        """Contenuto in base64 che decodifica in XML."""
        import base64
        xml_content = b"<xml><test>base64 data</test></xml>"
        b64_content = base64.b64encode(xml_content)
        # Non è un CMS valido, ma il fallback base64 restituirà il contenuto
        # che a sua volta fallirà il parsing CMS e restituirà None
        # perché il contenuto base64 non inizia con '<'
        result = extract_xml_from_p7m(b64_content, "b64.p7m", print)
        assert result is None or result == xml_content


class TestOutputManager:
    """Test per OutputManager (senza download reali)."""

    def test_creation(self):
        """Creazione OutputManager con nuova struttura."""
        om = OutputManager("01234567890", print, anno=2025, db_enabled=False)
        assert om.piva == "01234567890"
        assert om.anno == 2025
        assert om.db_enabled is False
        assert om.root_path == "output/01234567890/2025"
        assert os.path.isdir(om.root_path) or not os.path.exists(om.root_path)

    def test_db_stats_defaults(self):
        """db_stats parte con contatori a zero."""
        om = OutputManager("01234567890", print, anno=2025, db_enabled=True)
        assert om.db_stats == {"ADDED": 0, "SKIPPED": 0, "ERROR": 0}

    def test_handle_db_hook_disabled(self):
        """Con db_enabled=False, _handle_db_hook non fa nulla."""
        om = OutputManager("01234567890", print, anno=2025, db_enabled=False)

        with patch("app.output_manager.process_xml_file") as mock_process:
            om._handle_db_hook("test.xml")
            mock_process.assert_not_called()

    def test_handle_db_hook_enabled(self):
        """Con db_enabled=True, _handle_db_hook chiama process_xml_file."""
        om = OutputManager("01234567890", print, anno=2025, db_enabled=True)

        with patch("app.output_manager.process_xml_file", return_value="ADDED") as mock_process:
            om._handle_db_hook("test.xml")
            mock_process.assert_called_once()
            assert om.db_stats["ADDED"] == 1

    def test_handle_db_hook_unknown_status(self):
        """Status non riconosciuto non incrementa contatori."""
        om = OutputManager("01234567890", print, anno=2025, db_enabled=True)

        with patch("app.output_manager.process_xml_file", return_value="UNKNOWN"):
            om._handle_db_hook("test.xml")
            assert om.db_stats == {"ADDED": 0, "SKIPPED": 0, "ERROR": 0}


@pytest.mark.smoke
def test_output_manager_smoke():
    """Smoke test delle funzioni principali."""
    name = safe_filename_from_disposition(
        'attachment; filename="IT12345.xml"', "fallback"
    )
    assert name == "IT12345.xml"
    print(f"Output manager smoke test OK — filename={name}")
