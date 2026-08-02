import os
from pathlib import Path
from types import SimpleNamespace

from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel


def test_web_channel_refreshes_changed_ui_bundle(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text("first __PICO_WS_PORT__ __PICO_API_PORT__", encoding="utf-8")
    channel = WebChannel(SimpleNamespace(port=19001), MessageBus())
    channel._index_path = index
    channel._index_html = channel._render_index_html(19001)
    channel._index_mtime_ns = index.stat().st_mtime_ns

    assert channel._current_index_html() == "first 19001 19002"

    index.write_text("second __PICO_WS_PORT__ __PICO_API_PORT__", encoding="utf-8")
    timestamp = index.stat().st_mtime_ns
    os.utime(index, ns=(timestamp + 1, timestamp + 1))

    assert channel._current_index_html() == "second 19001 19002"
