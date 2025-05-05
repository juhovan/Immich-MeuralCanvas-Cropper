import logging
import time
import requests
import urllib.request

import config

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

    def upload_image(self, image_path, path="/items"):
        if (self.token_time - time.time()) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        files = {'image': open(image_path, 'rb')}
        r = requests.post(self.base_url + path, headers=headers, files=files).json()

        logging.info(f"Meural upload response: {r}")
        if "error" in r:
            logging.error(f"Error uploading image to Meural: {r['error']}")
            return False

        return(r)




