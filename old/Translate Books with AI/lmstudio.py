import requests
import time
import logging
import os
import subprocess

API_BASE = "http://localhost:1234"
MODELS_ENDPOINT = "/v1/models"
HEALTH_ENDPOINTS = ["/health", "/v1/health", "/status"]

logging.basicConfig(
    filename="translator.log",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


def find_lm_studio_path():
    """Try to automatically locate LM Studio installation."""
    possible_paths = [
        r"C:\Program Files\LM Studio\LM Studio.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\LM Studio\LM Studio.exe"),
        os.path.expanduser(r"~\AppData\Local\LM Studio\LM Studio.exe"),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            logging.info(f"Auto-detected LM Studio path: {path}")
            return path

    logging.error("LM Studio executable not found automatically.")
    return None


def restart_lm_studio(path):
    """Force-restart LM Studio when API becomes unresponsive."""
    logging.warning("Restarting LM Studio...")

    try:
        os.system('taskkill /IM "LM Studio.exe" /F')
        logging.info("LM Studio process terminated.")
    except Exception as e:
        logging.error(f"Failed to terminate LM Studio: {e}")

    time.sleep(10)

    try:
        subprocess.Popen([path])
        logging.info("LM Studio restarted successfully.")
    except Exception as e:
        logging.error(f"Failed to restart LM Studio: {e}")

    # Give LM Studio time to initialize API
    time.sleep(15)


# -------------------------
# API health + model list
# -------------------------

def _probe_health(api_base=API_BASE, timeout=3):
    """Try several common health endpoints. Return True if any respond 200."""
    for ep in HEALTH_ENDPOINTS:
        url = api_base.rstrip("/") + ep
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                logging.info(f"LM Studio health OK at {url}")
                return True
        except Exception:
            continue
    # fallback: try root
    try:
        r = requests.get(api_base, timeout=timeout)
        if r.status_code == 200:
            logging.info(f"LM Studio root responded OK at {api_base}")
            return True
    except Exception:
        pass
    logging.warning("LM Studio health check failed")
    return False


def get_model_list(api_base=API_BASE, timeout=5):
    """
    Query LM Studio for available models.
    Returns a list of model names (strings). On failure returns an empty list.
    """
    url = api_base.rstrip("/") + MODELS_ENDPOINT
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        # Common shapes: {"data":[{"id":"model-name",...}, ...]} or {"models":[...]}
        models = []
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                for item in data["data"]:
                    # item may be dict with id or name
                    if isinstance(item, dict):
                        name = item.get("id") or item.get("name")
                        if name:
                            models.append(name)
                    elif isinstance(item, str):
                        models.append(item)
            elif "models" in data and isinstance(data["models"], list):
                for item in data["models"]:
                    if isinstance(item, dict):
                        name = item.get("id") or item.get("name")
                        if name:
                            models.append(name)
                    elif isinstance(item, str):
                        models.append(item)
            else:
                # try to flatten keys
                for k, v in data.items():
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                name = item.get("id") or item.get("name")
                                if name:
                                    models.append(name)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    name = item.get("id") or item.get("name")
                    if name:
                        models.append(name)
                elif isinstance(item, str):
                    models.append(item)

        # dedupe and sort
        models = sorted(list(dict.fromkeys(models)))
        logging.info(f"Discovered {len(models)} models from LM Studio")
        return models
    except Exception as e:
        logging.error(f"Failed to fetch model list from {url}: {e}")
        return []


def wait_for_api(api_base=API_BASE, timeout=30, poll_interval=2):
    """
    Wait up to `timeout` seconds for LM Studio API to become healthy.
    Returns True if healthy, False otherwise.
    """
    start = time.time()
    while time.time() - start < timeout:
        if _probe_health(api_base):
            return True
        time.sleep(poll_interval)
    return False
