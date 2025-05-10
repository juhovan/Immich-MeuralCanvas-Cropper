import logging
import time
import requests
import urllib.request

import config
from datetime import datetime

class MeuralUpload:
    def __init__(self, meural_username, meural_password):
        self.username = meural_username
        self.password = meural_password
        self.token = None
        self.token_time = None
        self.base_url = "https://api.meural.com/v0"
        self.authenticate()

    def authenticate(self, path="/authenticate"):
        req = requests.post(
            self.base_url + path, data={"username": self.username, "password": self.password}
        ).json()
        self.token = req["token"]
        self.token_time = time.time()

    # Adding information about user agent
    # chance otherwise is that website block when they see python trying to access
    opener=urllib.request.build_opener()
    opener.addheaders=[('User-Agent','Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/36.0.1941.0 Safari/537.36')]
    urllib.request.install_opener(opener)

    def upload_image_data(self, image_path, path="/items"):
        if (self.token_time - time.time()) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        files = {'image': open(image_path, 'rb')}
        r = requests.post(self.base_url + path, headers=headers, files=files).json()

        logging.info(f"Meural upload response: {r}")
        if "error" in r:
            logging.error(f"Error uploading image to Meural: {r['error']}")
            return False

        return r.get("data", {}).get("id")

    def set_image_metadata(self, image_id, name, description, medium, year):
        if (self.token_time - time.time()) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        data = {
            "name": name,
            "description": description,
            "medium": medium,
            "year": year
        }
        r = requests.put(
            f"{self.base_url}/items/{image_id}",
            headers=headers,
            json=data
        ).json()

        logging.info(f"Meural metadata response: {r}")
        if "error" in r:
            logging.error(f"Error setting metadata for image {image_id}: {r['error']}")
            return False
        return True

    def upload_image(self, image_path, metadata):
        image_id = self.upload_image_data(image_path)
        if not image_id:
            return False

        exif = metadata["exif"]
        name = (exif.get("description") or metadata["original_filename"])
        medium = ((exif.get("city") or "") + " " +
                  (exif.get("state") or "") + " " +
                  (exif.get("country") or "")).strip()
        raw_date = metadata["local_date_time"]
        try:
            raw_date = raw_date.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw_date)
            year = dt.strftime("%d.%m.%Y")
        except Exception:
            year = raw_date[:16] if len(raw_date) > 16 else raw_date

        make = exif.get("make") or ""
        model = exif.get("model") or ""
        lens_model = exif.get("lensModel") or ""
        exposure_time = "" if exif.get("exposureTime") is None else f"{exif.get('exposureTime')}s "
        f_number = "" if exif.get("fNumber") is None else f"f/{exif.get('fNumber')} "
        iso = "" if exif.get("iso") is None else f"ISO{exif.get('iso')} "
        focal_length = "" if exif.get("focalLength") is None else f"{exif.get('focalLength')}mm"

        description = f"{make} {model} {lens_model} {exposure_time}{f_number}{iso}{focal_length}".strip()

        self.set_image_metadata(image_id, name, description, medium, year)
        return True
