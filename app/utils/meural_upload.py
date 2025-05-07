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

    def set_image_metadata(self, image_id, title, description, medium, year):
        if (self.token_time - time.time()) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        data = {
            "title": title,
            "description": description,
            "medium": medium,
            "year": year
        }
        r = requests.put(
            f"{self.base_url}/items/{image_id}",
            headers=headers,
            json=data
        ).json()

    def upload_image(self, image_path, metadata):
        image_id = self.upload_image_data(image_path)
        if not image_id:
            return False

        exif = metadata["exif"]
        title = exif.get("description", metadata["original_filename"])
        medium = exif.get("city", "") + " " + exif.get("state", "") + " " + exif.get("country", "")
        raw_date = metadata["local_date_time"]
        try:
            dt = datetime.fromisoformat(raw_date)
            year = dt.strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            year = raw_date
        description = (
            exif.get("make", "") + " " + exif.get("model", "") + " " +
            exif.get("lens_model", "") + " " + exif.get("exposure_time", "") + "s f/" +
            exif.get("f_number", "") + " ISO" + exif.get("iso", "") + " " +
            exif.get("focal_length", "") + "mm"
        )
        self.set_image_metadata(image_id, title, description, medium, year)
        return True
