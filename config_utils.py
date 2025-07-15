# config_utils.py

import json
from typing import Optional, Dict, Any

CONFIG_FILE_PATH = 'configs.json'

def save_config(server_id: str, channel_id: str, new_channel_config: Dict[str, Any]):
    """Salva a configuração de um canal específico no arquivo JSON."""
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            configs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        configs = {}

    server_id_str = str(server_id)
    channel_id_str = str(channel_id)

    server_config = configs.get(server_id_str, {})
    if 'channels' not in server_config:
        server_config['channels'] = {}

    channel_config = server_config["channels"].get(channel_id_str, {})
    channel_config.update(new_channel_config)
    server_config["channels"][channel_id_str] = channel_config
    configs[server_id_str] = server_config

    with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(configs, f, indent=4)

def load_config(server_id: str, channel_id: str) -> Optional[Dict[str, Any]]:
    """Carrega a configuração de um canal específico do arquivo JSON."""
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        return configs.get(str(server_id), {}).get("channels", {}).get(str(channel_id))
    except FileNotFoundError:
        return None