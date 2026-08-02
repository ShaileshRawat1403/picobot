from picobot.cli import commands


def test_resolve_web_port_reuses_existing_pico(monkeypatch, capsys):
    monkeypatch.setattr(commands, "_pico_web_running", lambda host, port: True)

    assert commands._resolve_web_port("127.0.0.1", 18791) is None
    assert "already running" in capsys.readouterr().out


def test_resolve_web_port_finds_next_pair(monkeypatch, capsys):
    monkeypatch.setattr(commands, "_pico_web_running", lambda host, port: False)
    available = {18793}
    monkeypatch.setattr(
        commands,
        "_web_pair_available",
        lambda host, port: port in available,
    )

    assert commands._resolve_web_port("127.0.0.1", 18791) == 18793
    assert "using 18793/18794" in capsys.readouterr().out


def test_web_pair_available_rejects_invalid_port():
    assert commands._web_pair_available("127.0.0.1", 0) is False
    assert commands._web_pair_available("127.0.0.1", 65535) is False
