"""
Test per app/config.py — ConfigManager.
"""

import os
import tempfile
import pytest

from app.config import ConfigManager, ENV_MAP, HOME_CONFIG_DIR


class TestConfigManager:
    """Test del ConfigManager."""

    def _clean_env(self):
        """Rimuove variabili FEC_* che potrebbero interferire."""
        for key in list(os.environ.keys()):
            if key.startswith("FEC_"):
                del os.environ[key]

    def test_load_empty(self):
        """Un ConfigManager senza sorgenti restituisce valori vuoti."""
        self._clean_env()
        cfg = ConfigManager()
        # Non chiamiamo load() — verificiamo comportamento pre-caricamento
        assert cfg.get("CF") == ""
        assert cfg.get("PIN", "default") == "default"
        assert cfg.is_loaded is False

    def test_load_with_override_file(self):
        """Caricamento da un file .env esplicito."""
        self._clean_env()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write("# Test config\n")
            f.write("CF=TEST_CF\n")
            f.write("PIN=TEST_PIN\n")
            f.write("PASSWORD=TEST_PASSWORD\n")
            f.write("PIVA=01234567890\n")
            f.write("DATA_DAL=01/01/2025\n")
            f.write("DATA_AL=31/12/2025\n")
            f.write("UTENZA=3\n")
            env_path = f.name

        try:
            cfg = ConfigManager()
            cfg.load(env_override=env_path)
            assert cfg.is_loaded is True
            assert cfg.get("CF") == "TEST_CF", f"CF={cfg.get('CF')}"
            assert cfg.get("PIN") == "TEST_PIN"
            assert cfg.get("PASSWORD") == "TEST_PASSWORD"
            assert cfg.get("PIVA") == "01234567890"
            assert cfg.get("DATA_DAL") == "01/01/2025"
            assert cfg.get("DATA_AL") == "31/12/2025"
            assert cfg.get("UTENZA") == "3"
            assert "file:" + env_path in str(cfg.sources)
        finally:
            os.unlink(env_path)

    def test_env_vars_override(self):
        """Le variabili d'ambiente FEC_* hanno la massima priorità."""
        self._clean_env()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write("CF=FILE_CF\n")
            f.write("PIN=FILE_PIN\n")
            f.write("PASSWORD=FILE_PASSWORD\n")
            env_path = f.name

        os.environ["FEC_CF"] = "ENV_CF"
        os.environ["FEC_PIN"] = "ENV_PIN"

        try:
            cfg = ConfigManager()
            cfg.load(env_override=env_path)
            # Le env var sovrascrivono il file
            assert cfg.get("CF") == "ENV_CF"
            assert cfg.get("PIN") == "ENV_PIN"
            # PASSWORD non ha env var, prende dal file
            assert cfg.get("PASSWORD") == "FILE_PASSWORD", f"PASSWORD={cfg.get('PASSWORD')}"
        finally:
            os.unlink(env_path)
            del os.environ["FEC_CF"]
            del os.environ["FEC_PIN"]

    def test_get_int(self):
        """get_int converte correttamente i valori."""
        cfg = ConfigManager()
        cfg._data = {"DB": "1", "DAILY": "0", "NOT_AN_INT": "abc"}
        cfg._loaded = True

        assert cfg.get_int("DB") == 1
        assert cfg.get_int("DAILY") == 0
        assert cfg.get_int("NOT_AN_INT") == 0
        assert cfg.get_int("MISSING", 42) == 42

    def test_get_bool(self):
        """get_bool interpreta correttamente 1/0, true/false."""
        cfg = ConfigManager()
        cfg._data = {"DB": "1", "DAILY": "0", "FLAG": "true", "NO": "false"}
        cfg._loaded = True

        assert cfg.get_bool("DB") is True
        assert cfg.get_bool("DAILY") is False
        assert cfg.get_bool("FLAG") is True
        assert cfg.get_bool("NO") is False
        assert cfg.get_bool("MISSING") is False
        assert cfg.get_bool("MISSING", True) is True

    def test_get_list(self):
        """get_list splitta correttamente separatori."""
        cfg = ConfigManager()
        cfg._data = {"ITEMS": "a,b,c", "SINGLE": "solo"}
        cfg._loaded = True

        assert cfg.get_list("ITEMS") == ["a", "b", "c"]
        assert cfg.get_list("SINGLE") == ["solo"]
        assert cfg.get_list("MISSING") == []

    def test_to_dict(self):
        """to_dict restituisce una copia del dict interno."""
        cfg = ConfigManager()
        cfg._data = {"CF": "test", "PIN": "1234"}
        cfg._loaded = True

        d = cfg.to_dict()
        assert d == {"CF": "test", "PIN": "1234"}
        # La modifica della copia non altera l'originale
        d["CF"] = "modificato"
        assert cfg.get("CF") == "test"

    def test_validate_ok(self):
        """validate non solleva eccezioni se i campi sono presenti."""
        cfg = ConfigManager()
        cfg._data = {"CF": "x", "PIN": "y", "PASSWORD": "z"}
        cfg._loaded = True
        # Non deve sollevare eccezioni
        cfg.validate(["CF", "PIN", "PASSWORD"])

    def test_validate_missing(self):
        """validate solleva ValueError se mancano campi."""
        cfg = ConfigManager()
        cfg._data = {"CF": "x"}
        cfg._loaded = True

        with pytest.raises(ValueError, match="PIN"):
            cfg.validate(["CF", "PIN"])

    def test_get_required_ok(self):
        """get_required restituisce il valore se presente."""
        cfg = ConfigManager()
        cfg._data = {"CF": "test_cf"}
        cfg._loaded = True

        assert cfg.get_required("CF") == "test_cf"

    def test_get_required_missing(self):
        """get_required solleva ValueError se il campo è assente."""
        cfg = ConfigManager()
        cfg._loaded = True

        with pytest.raises(ValueError, match="PIVA"):
            cfg.get_required("PIVA")

    def test_env_map_completeness(self):
        """Verifica che ENV_MAP contenga tutte le chiavi principali."""
        required_keys = [
            "FEC_CF", "FEC_PIN", "FEC_PASSWORD", "FEC_PIVA",
            "FEC_DATA_DAL", "FEC_DATA_AL", "FEC_UTENZA", "FEC_TIPO",
            "FEC_DB", "FEC_WRITE", "FEC_CORRISPETTIVI", "FEC_TRANSFRONTALIERE",
            "FEC_DB_TYPE", "FEC_DB_SQLITE_PATH",
        ]
        for key in required_keys:
            assert key in ENV_MAP, f"Manca {key} in ENV_MAP"
            assert ENV_MAP[key] is not None and len(ENV_MAP[key]) > 0

    def test_home_config_dir_constant(self):
        """HOME_CONFIG_DIR punta a ~/.fec_ade."""
        assert HOME_CONFIG_DIR.endswith(".fec_ade")
        assert os.path.isabs(HOME_CONFIG_DIR)


@pytest.mark.smoke
def test_config_manager_smoke():
    """Smoke test: ConfigManager si carica senza errori."""
    cfg = ConfigManager()
    assert isinstance(cfg, ConfigManager)
    assert callable(cfg.load)
    assert callable(cfg.get)
    assert callable(cfg.get_int)
    assert callable(cfg.get_bool)
    assert callable(cfg.validate)
    print("ConfigManager API OK — metodi: load, get, get_int, get_bool, validate, to_dict")
