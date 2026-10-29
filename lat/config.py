import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "device": "Default output (what you hear)",
    "source": "Auto-detect",
    "target": "English",
    "provider": "Google Translate (built-in, free)",
    "model": "small",
    "show_partials": True,
    "silence_s": 0.6,
    "max_segment_s": 7.0,
    "partial_interval_s": 1.2,
    "sensitivity": 1.0,
    "overlay": True,
    "overlay_show_source": True,
    "overlay_font": 26,
    "overlay_alpha": 0.85,
    "overlay_geometry": "",
    "window_geometry": "",
    "keys": {},  # provider settings / API keys (stored locally in this file)
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)
