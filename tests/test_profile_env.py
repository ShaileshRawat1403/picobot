from __future__ import annotations

import json

from picobot.config.loader import load_config


def test_profile_local_env_supplies_openai_key_without_json_secret(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": "gpt-4.1-mini", "provider": "openai"}},
                "providers": {"openai": {"apiKey": ""}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.providers.openai.api_key == "test-key"
