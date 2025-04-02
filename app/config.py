import os
import tempfile
import yaml


# Load config
def load_config():
    config_paths = [
        "/config/config.yaml",  # Docker mounted config
        os.path.join(
            os.path.dirname(__file__), "config", "config.yaml"
        ),  # Local development
    ]

    for config_path in config_paths:
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                return yaml.safe_load(f)

    raise ValueError(f"config.yaml not found in: {', '.join(config_paths)}")


config = load_config()

# Application paths (under /tmp to handle container temp storage)
APP_TMP_DIR = os.path.join(tempfile.gettempdir(), "meural-cropper")
INPUT_FOLDER = os.path.join(APP_TMP_DIR, "input")
OUTPUT_FOLDER = os.path.join(APP_TMP_DIR, "output")
TEMP_FOLDER = os.path.join(APP_TMP_DIR, "temp")
STATIC_FOLDER = os.path.join(os.path.dirname(__file__), "static")
# Image dimensions
PORTRAIT_SIZE = tuple(config["dimensions"]["portrait_size"])  # (width, height)
LANDSCAPE_SIZE = tuple(config["dimensions"]["landscape_size"])  # (width, height)

# Immich configuration
IMMICH_URL = config["immich"]["url"]
IMMICH_API_KEY = config["immich"]["api_key"]
IMMICH_INPUT_ALBUM_ID = config["immich"]["input_album_id"]
IMMICH_OUTPUT_ALBUM_ID = config["immich"]["output_album_id"]

# Meural configuration
MEURAL_DEVICES = config.get("meural", {}).get("devices", [])

# File paths
PROGRESS_FILE = os.path.join(TEMP_FOLDER, "progress.json")


# Ensure required directories exist
def init_directories():
    """Create necessary directories if they don't exist"""
    os.makedirs(INPUT_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(TEMP_FOLDER, exist_ok=True)
    os.makedirs(STATIC_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, "portrait"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, "landscape"), exist_ok=True)
