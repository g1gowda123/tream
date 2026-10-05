import os
from unittest.mock import patch
from tream.core.config import get_config_dir, is_termux, load_config


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("TREAM_BUFFER", "1G")
    monkeypatch.setenv("TREAM_PLAYER", "vlc")
    monkeypatch.setenv("TREAM_PORT", "9090")
    monkeypatch.setenv("TREAM_TORRSERVER_BIN", "/custom/bin/torrserver")

    cfg = load_config()

    assert cfg["playback"]["buffer"] == "1G"
    assert cfg["general"]["player"] == "vlc"
    assert cfg["general"]["torrserver_port"] == 9090
    assert cfg["general"]["torrserver_binary"] == "/custom/bin/torrserver"


def test_is_termux_detection(monkeypatch):
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert is_termux() is True

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    # Without TERMUX_VERSION and assuming /data/data/com.termux doesn't exist on this linux machine
    if not os.path.exists("/data/data/com.termux"):
        assert is_termux() is False
