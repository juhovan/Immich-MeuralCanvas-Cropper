import logging
import time
import requests
import urllib.request

import config
from datetime import datetime, timezone, timedelta
# New imports for comparison helpers
from typing import Set, Dict, Any, Optional, List
from utils.file_handler import get_asset_metadata, read_all_crop_metadata
from utils.image_processor import crop_image
import boto3
# New imports for sync helpers
import os
from utils.immich_handler import ImmichHandler

class MeuralUpload:
    def __init__(self, meural_username, meural_password):
        self.username = meural_username
        self.password = meural_password
        self.token = None
        self.token_time = None
        self.base_url = "https://api.meural.com/v0"
        self.authenticate()
        # Lazy Immich handler (created only when needed)
        self._immich = None

    def authenticate(self, path="/authenticate"):
        try:
            client = boto3.client("cognito-idp", region_name="eu-west-1")
            response = client.initiate_auth(
                ClientId="487bd4kvb1fnop6mbgk8gu5ibf",
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": self.username, "PASSWORD": self.password},
            )
            if "AuthenticationResult" in response:
                logging.info("Authentication: Successfully authenticated with Meural API")
                self.token = response["AuthenticationResult"]["AccessToken"]
                self.token_time = time.time()
                return self.token
            else:
                logging.error("Authentication failed: No AuthenticationResult in response.")
                return None
        except client.exceptions.NotAuthorizedException as auth_err:
            logging.error(f"Authentication: Not authorized: {auth_err}")
            return None
        except client.exceptions.UserNotFoundException as user_err:
            logging.error(f"Authentication: User not found: {user_err}")
            return None
        except Exception as e:
            logging.error(f"Authentication: Unexpected error during authentication: {e}")
            return None


    # Adding information about user agent
    # chance otherwise is that website block when they see python trying to access
    opener=urllib.request.build_opener()
    opener.addheaders=[('User-Agent','Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/36.0.1941.0 Safari/537.36')]
    urllib.request.install_opener(opener)

    # Generic JSON request with retry/backoff to handle empty/invalid JSON bodies
    def _json_request_with_retry(
        self,
        method: str,
        url: str,
        retries: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        expect_json: bool = True,
        **kwargs
    ):
        last_exc = None
        for attempt in range(retries):
            try:
                resp = requests.request(method, url, **kwargs)
                if not expect_json:
                    return None, resp
                try:
                    data = resp.json()
                    return data, resp
                except ValueError as ve:
                    last_exc = ve
                    wait = delay * (backoff ** attempt)
                    logging.warning(
                        f"JSON decode failed for {method} {url} (attempt {attempt+1}/{retries}): {ve}. Retrying in {wait:.1f}s"
                    )
                    time.sleep(wait)
                    continue
            except requests.RequestException as rexc:
                last_exc = rexc
                wait = delay * (backoff ** attempt)
                logging.warning(
                    f"Request failed for {method} {url} (attempt {attempt+1}/{retries}): {rexc}. Retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
        logging.error(f"Giving up {method} {url} after {retries} attempts: {last_exc}")
        return None, None

    def _upload_image_data(self, image_path, path="/items"):
        if (time.time() - self.token_time) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        # Use context manager to avoid file descriptor leaks
        with open(image_path, 'rb') as fh:
            files = {'image': fh}
            r, _ = self._json_request_with_retry("POST", self.base_url + path, headers=headers, files=files)
        if r is None:
            logging.error("Error uploading image to Meural: invalid/empty JSON response")
            return False

        logging.info(f"Meural upload response: {r}")
        if "error" in r:
            logging.error(f"Error uploading image to Meural: {r['error']}")
            return False

        return r.get("data", {}).get("id")

    def _set_image_metadata(self, image_id, name, author, description, medium, year):
        if (time.time() - self.token_time) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        data = {
            "name": name,
            "author": author or "",
            "description": description,
            "medium": medium,
            "year": year
        }
        r, _ = self._json_request_with_retry(
            "PUT",
            f"{self.base_url}/items/{image_id}",
            headers=headers,
            json=data
        )
        if r is None:
            logging.error(f"Error setting metadata for image {image_id}: invalid/empty JSON response")
            return False

        logging.info(f"Meural metadata response: {r}")
        if "error" in r:
            logging.error(f"Error setting metadata for image {image_id}: {r['error']}")
            return False
        return True

    def _add_to_playlist(self, image_id, playlist_id):
        if (time.time() - self.token_time) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        _, resp = self._json_request_with_retry(
            "POST",
            f"{self.base_url}/galleries/{playlist_id}/items/{image_id}",
            headers=headers,
            expect_json=False,
            timeout=20,
        )

        if resp is None:
            logging.error(f"Error adding image {image_id} to playlist {playlist_id}: no response")
            return False

        if resp.status_code in (200, 201, 204, 409):
            logging.info(
                f"Meural add to playlist response: status={resp.status_code} image_id={image_id}"
            )
            return True

        logging.error(
            f"Error adding image {image_id} to playlist {playlist_id}: {resp.status_code} {resp.text[:200]}"
        )
        return False

    def _format_exif_description(self, exif: Dict[str, Any]) -> str:
        """Format EXIF data into a description string."""
        make = exif.get("make") or ""
        model = exif.get("model") or ""
        if make and model:
            if model.lower().startswith(make.lower()):
                model = model[len(make):].strip()
            elif make.lower() in model.lower():
                model = " ".join(
                    [part for part in model.split() if part.lower() != make.lower()]
                ).strip()
        lens_model = exif.get("lensModel") or ""
        exposure_time = "" if exif.get("exposureTime") is None else f"{exif.get('exposureTime')}s "
        f_number = "" if exif.get("fNumber") is None else f"f/{exif.get('fNumber')} "
        iso = "" if exif.get("iso") is None else f"ISO{exif.get('iso')} "
        focal_length = "" if exif.get("focalLength") is None else f"{exif.get('focalLength')}mm"

        return f"{make} {model}, {lens_model}, {exposure_time}{f_number}{iso}{focal_length}".strip()

    def _metadata_changed(self, current_metadata: Dict[str, Any], current_description: str) -> bool:
        """Compare relevant metadata fields directly with what's in Meural description."""
        try:
            expected_desc = self._format_exif_description(current_metadata.get("exif", {}))
            actual_desc = current_description.rsplit('\n', 1)[0].strip()
            return expected_desc != actual_desc
        except Exception as e:
            logging.error(f"Error comparing metadata: {e}")
            return False

    def _build_author_from_people(self, people: List[Dict[str, Any]]) -> str:
        if not people:
            return ""

        ordered = []
        for person in people:
            name = (person or {}).get("name") or ""
            if not name:
                continue
            faces = (person or {}).get("faces") or []
            x_vals = [f.get("boundingBoxX1") for f in faces if f.get("boundingBoxX1") is not None]
            x_pos = min(x_vals) if x_vals else float("inf")
            ordered.append((x_pos, name))

        ordered.sort(key=lambda item: item[0])
        return ", ".join([name for _, name in ordered])

    def _extract_album_names(self, albums: Any) -> List[str]:
        names: List[str] = []
        if not albums:
            return names
        for album in albums:
            if isinstance(album, str):
                if album:
                    names.append(album)
                continue
            if isinstance(album, dict):
                name = album.get("albumName") or album.get("name") or album.get("title")
                if name:
                    names.append(name)
        return names

    def _get_album_names_for_asset(self, metadata: Dict[str, Any]) -> List[str]:
        names = self._extract_album_names(metadata.get("albums") or metadata.get("albumInfo"))
        if names:
            return names

        album_ids = metadata.get("album_ids") or metadata.get("albumIds") or []
        if album_ids:
            try:
                immich = self._get_immich()
                resolved = []
                for album_id in album_ids:
                    try:
                        album_info = immich._make_request("GET", f"/albums/{album_id}")
                        name = album_info.get("albumName") or album_info.get("name") or album_info.get("title")
                        if name:
                            resolved.append(name)
                    except Exception:
                        continue
                if resolved:
                    return resolved
            except Exception as e:
                logging.error(f"Failed to resolve album IDs from metadata: {e}")

        asset_id = metadata.get("asset_id")
        if not asset_id:
            return []

        try:
            immich = self._get_immich()
            albums = immich.get_asset_albums(asset_id)
            return self._extract_album_names(albums)
        except Exception as e:
            logging.error(f"Failed to fetch albums for asset {asset_id}: {e}")
            return []

    def _parse_timezone_offset(self, tz_value: str):
        if not tz_value:
            return None

        tz_value = tz_value.strip()
        if tz_value in ("Z", "UTC"):
            return datetime.timezone.utc

        # Accept formats like +02:00, -0500, UTC+02:00
        if tz_value.upper().startswith("UTC"):
            tz_value = tz_value[3:]

        sign = 1
        if tz_value.startswith("-"):
            sign = -1
        tz_value = tz_value.lstrip("+-")

        parts = tz_value.split(":")
        try:
            if len(parts) == 1:
                hours = int(parts[0][:2])
                minutes = int(parts[0][2:]) if len(parts[0]) > 2 else 0
            else:
                hours = int(parts[0])
                minutes = int(parts[1])
        except Exception:
            return None

        offset = datetime.timedelta(hours=hours * sign, minutes=minutes * sign)
        return datetime.timezone(offset)

    def _get_people_for_asset(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        people = metadata.get("people") or []
        if people:
            return people

        asset_id = metadata.get("asset_id")
        if not asset_id:
            return []

        try:
            immich = self._get_immich()
            asset_info = immich._make_request("GET", f"/assets/{asset_id}")
            return asset_info.get("people", []) if isinstance(asset_info, dict) else []
        except Exception as e:
            logging.error(f"Failed to fetch people for asset {asset_id}: {e}")
            return []

    def upload_image(self, image_path, metadata):
        image_id = self._upload_image_data(image_path)
        if not image_id:
            return False

        exif = metadata["exif"]
        name = (exif.get("description") or metadata["original_filename"])
        medium_parts = [
            exif.get("city"),
            exif.get("state"),
            exif.get("country"),
        ]
        medium = ", ".join([part for part in medium_parts if part]).strip()
        author = self._build_author_from_people(self._get_people_for_asset(metadata))
        raw_date = metadata["local_date_time"]
        try:
            raw_date = raw_date.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw_date)
            if dt.tzinfo is None:
                tz_value = (metadata.get("exif", {}) or {}).get("timeZone")
                tzinfo = self._parse_timezone_offset(tz_value)
                if tzinfo is not None:
                    dt = dt.replace(tzinfo=tzinfo)
            year = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            year = raw_date[:16] if len(raw_date) > 16 else raw_date

        # Use asset_id instead of original_filename in the last line of the description
        # This is our unique identifier for matching between Immich and Meural
        asset_id = metadata.get("asset_id", "")
        if not asset_id:
            # Fallback to original_filename only if we must
            asset_id = metadata["original_filename"]

        album_names = self._get_album_names_for_asset(metadata)
        album_list = ", ".join([name for name in album_names if name])
        metadata_desc = self._format_exif_description(exif)
        description_parts = []
        if album_list:
            description_parts.append(f"Albums: {album_list}")
        if metadata_desc:
            description_parts.append(metadata_desc)
        description_line = " | ".join([part for part in description_parts if part]).strip()
        if description_line:
            description = f"{description_line}\n{asset_id}".strip()
        else:
            description = asset_id

        set_image_metdata = self._set_image_metadata(image_id, name, author, description, medium, year)
        add_to_playlist = self._add_to_playlist(image_id, config.MEURAL_PLAYLIST_ID)
        if not set_image_metdata or not add_to_playlist:
            logging.error(f"Failed to set metadata or add image {image_id} to playlist.")
            return False
        logging.info(f"Image {image_id} uploaded and metadata set successfully.")
        return True

    # Helper: list all items in a Meural playlist (returns list of dicts)
    def _list_playlist_items(self, playlist_id: str, per_page: int = 10) -> List[Dict[str, Any]]:
        try:
            if not self.token_time or (time.time() - self.token_time) > 300:
                self.authenticate()
            headers = {"Authorization": f"Token {self.token}"}
            items_all: List[Dict[str, Any]] = []
            page = 1
            total_pages = None
            while True:
                url = f"{self.base_url}/galleries/{playlist_id}/items"
                params = {"page": page, "per_page": per_page}
                data, resp = self._json_request_with_retry("GET", url, headers=headers, params=params, timeout=15)
                if data is None:
                    logging.error(f"Failed to fetch playlist items (page {page}) due to invalid/empty JSON")
                    break
                if resp is not None and not resp.ok:
                    logging.error(f"Failed to fetch playlist items: {resp.status_code} {resp.text[:200]}")
                    break
                items = data.get("data")
                if items is None:
                    items = data if isinstance(data, list) else data.get("items", [])
                if not items:
                    break
                items_all.extend(items)
                is_paginated = data.get("isPaginated")
                is_last = data.get("isLast")
                total_count = data.get("count")
                if is_last is True:
                    break
                if is_paginated is False:
                    if len(items) < per_page:
                        break
                if total_count is not None and len(items_all) >= int(total_count):
                    break
                page += 1
            return items_all
        except Exception as e:
            logging.error(f"Error listing playlist items: {e}")
            return []

    # Helper: remove an item from a playlist (non-destructive to the library)
    def _remove_from_playlist(self, image_id: int, playlist_id: str) -> bool:
        try:
            if not self.token_time or (time.time() - self.token_time) > 300:
                self.authenticate()
            headers = {"Authorization": f"Token {self.token}"}
            url = f"{self.base_url}/galleries/{playlist_id}/items/{image_id}"
            resp = requests.delete(url, headers=headers, timeout=15)
            if resp.ok:
                logging.info(f"Removed item {image_id} from playlist {playlist_id}")
                return True
            logging.error(f"Failed to remove item {image_id}: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:
            logging.error(f"Error removing item {image_id} from playlist: {e}")
            return False

    # Helper: lazy Immich handler
    def _get_immich(self) -> ImmichHandler:
        if self._immich is None:
            self._immich = ImmichHandler()
        return self._immich

    def _normalize_crop_metadata(self) -> Dict[str, Dict[str, Any]]:
        raw = read_all_crop_metadata()
        if isinstance(raw, dict) and "crops" in raw and isinstance(raw.get("crops"), dict):
            return raw.get("crops", {})
        return raw if isinstance(raw, dict) else {}

    def _get_input_album_asset_ids(self) -> Set[str]:
        try:
            immich = self._get_immich()
            assets = immich.get_album_assets(config.IMMICH_INPUT_ALBUM_ID)
            return {a.get("id") for a in assets if a.get("id")}
        except Exception as e:
            logging.error(f"Failed to read Immich input album assets: {e}")
            return set()

    def _get_meural_asset_map(
        self, playlist_id: str, per_page: int = 100
    ) -> Dict[str, Dict[str, Any]]:
        items = self._list_playlist_items(playlist_id, per_page=per_page)
        meural_map: Dict[str, Dict[str, Any]] = {}
        for it in items:
            desc = (it or {}).get("description") or ""
            lines = [ln.strip() for ln in desc.splitlines() if ln.strip()]
            if not lines:
                continue
            asset_id = lines[-1]
            entry = meural_map.setdefault(asset_id, {"item_ids": [], "description": ""})
            entry["item_ids"].append(it.get("id"))
            entry["description"] = "\n".join(lines[:-1])
        return meural_map

    def compare_playlist_with_input_album(
        self,
        playlist_id: Optional[str] = None,
        per_page: int = 100
    ) -> Dict[str, Any]:
        """
        Compare Meural playlist items with Immich input album, using crop metadata
        as the eligibility signal for upload.
        """
        playlist_id = playlist_id or getattr(config, "MEURAL_PLAYLIST_ID", None)
        if not playlist_id:
            logging.error("Meural playlist ID not provided")
            return {"error": "Meural playlist ID not provided"}

        # Ensure token is fresh
        try:
            if not self.token_time or (time.time() - self.token_time) > 300:
                self.authenticate()
        except Exception as e:
            logging.error(f"Failed to authenticate with Meural API: {e}")
            return {"error": f"Meural auth failed: {e}"}

        meural_map = self._get_meural_asset_map(playlist_id, per_page=per_page)
        meural_asset_ids = set(meural_map.keys())

        input_asset_ids = self._get_input_album_asset_ids()

        crop_metadata = self._normalize_crop_metadata()
        assets_with_crops = {
            asset_id for asset_id, data in crop_metadata.items() if data
        }

        eligible_for_upload = input_asset_ids & assets_with_crops

        missing_on_meural = sorted(list(eligible_for_upload - meural_asset_ids))
        only_on_meural = sorted(list(meural_asset_ids - input_asset_ids))
        in_both = sorted(list(meural_asset_ids & input_asset_ids))

        result = {
            "in_meural": sorted(list(meural_asset_ids)),
            "in_input_album": sorted(list(input_asset_ids)),
            "eligible_for_upload": sorted(list(eligible_for_upload)),
            "missing_on_meural": missing_on_meural,
            "only_on_meural": only_on_meural,
            "in_both": in_both,
            "counts": {
                "meural": len(meural_asset_ids),
                "input": len(input_asset_ids),
                "eligible": len(eligible_for_upload),
                "missing_on_meural": len(missing_on_meural),
                "only_on_meural": len(only_on_meural),
                "in_both": len(in_both),
            },
        }

        logging.info(
            "Meural vs Immich input comparison: Meural=%d Input=%d Eligible=%d MissingOnMeural=%d OnlyOnMeural=%d",
            result["counts"]["meural"],
            result["counts"]["input"],
            result["counts"]["eligible"],
            result["counts"]["missing_on_meural"],
            result["counts"]["only_on_meural"],
        )
        return result

    def sync_playlist_with_input_album(
        self,
        playlist_id: Optional[str] = None,
        per_page: int = 100
    ) -> Dict[str, Any]:
        """
        Make Meural playlist match Immich input album:
          - Upload missing assets that have crop metadata (generate crops on-demand)
          - Remove items present on Meural but missing from Immich input album
        """
        logging.info("Starting sync of Meural playlist with Immich input album...")
        playlist_id = playlist_id or getattr(config, "MEURAL_PLAYLIST_ID", None)

        if not playlist_id:
            return {"error": "playlist_id is required"}

        comparison = self.compare_playlist_with_input_album(playlist_id, per_page=per_page)
        if "error" in comparison:
            return comparison

        to_add = comparison.get("missing_on_meural", [])
        to_remove = comparison.get("only_on_meural", [])

        meural_map = self._get_meural_asset_map(playlist_id, per_page=per_page)
        crop_metadata = self._normalize_crop_metadata()

        removed = []
        added = []
        errors = []

        # Remove items no longer in input album
        for asset_id in to_remove:
            item_ids = meural_map.get(asset_id, {}).get("item_ids", [])
            for item_id in item_ids:
                ok = self._remove_from_playlist(item_id, playlist_id)
                if ok:
                    removed.append({"asset_id": asset_id, "item_id": item_id})
                else:
                    errors.append(f"Failed to remove item {item_id} for asset {asset_id}")

        # Add missing items (only if crop data exists)
        for asset_id in to_add:
            crop_data = crop_metadata.get(asset_id, {})
            if not crop_data:
                logging.info(f"Skipping {asset_id}: no crop metadata found")
                continue

            try:
                metadata = get_asset_metadata(asset_id)
            except Exception as e:
                errors.append(f"Missing asset metadata for {asset_id}: {e}")
                continue

            found_any = False
            for orientation in ["portrait", "landscape"]:
                if orientation not in crop_data:
                    continue

                out_name = f"{asset_id}_{orientation}.jpg"
                out_path = os.path.join(config.OUTPUT_FOLDER, orientation, out_name)

                if not os.path.exists(out_path):
                    try:
                        success, error = crop_image(asset_id, orientation, crop_data[orientation])
                        if not success:
                            errors.append(f"Failed to generate {orientation} crop for {asset_id}: {error}")
                            continue
                    except Exception as e:
                        errors.append(f"Error generating {orientation} crop for {asset_id}: {e}")
                        continue

                if os.path.exists(out_path):
                    ok = self.upload_image(out_path, metadata)
                    if ok:
                        added.append({"asset_id": asset_id, "path": out_path})
                        found_any = True
                    else:
                        errors.append(f"Failed to upload {out_path} for {asset_id}")

            if not found_any:
                errors.append(f"No cropped files uploaded for asset {asset_id}")

        summary = {
            "success": len(errors) == 0,
            "removed_count": len(removed),
            "added_count": len(added),
            "removed": removed,
            "added": added,
            "errors": errors,
            "counts_before": comparison.get("counts", {}),
        }
        logging.info(
            f"Sync summary: added={summary['added_count']} removed={summary['removed_count']} errors={len(errors)}"
        )
        if len(errors) > 0:
            logging.error(f"Errors during sync: {errors}")
        return summary

    def upload_from_crop_metadata(self) -> List[Dict[str, Any]]:
        """
        Upload all cropped images to Meural by generating them on-demand from metadata.
        Only assets still present in the Immich input album are uploaded.
        """
        uploaded = []
        crop_metadata = self._normalize_crop_metadata()
        if not crop_metadata:
            logging.info("No crop metadata found")
            return uploaded

        input_asset_ids = self._get_input_album_asset_ids()

        for asset_id, crop_data in crop_metadata.items():
            if asset_id not in input_asset_ids:
                continue

            try:
                metadata = get_asset_metadata(asset_id)
            except Exception as e:
                logging.error(f"Missing asset metadata for {asset_id}: {e}")
                continue

            for orientation in ["portrait", "landscape"]:
                if orientation not in crop_data:
                    continue

                out_name = f"{asset_id}_{orientation}.jpg"
                out_path = os.path.join(config.OUTPUT_FOLDER, orientation, out_name)

                if not os.path.exists(out_path):
                    success, error = crop_image(asset_id, orientation, crop_data[orientation])
                    if not success:
                        logging.error(f"Failed to generate {orientation} crop for {asset_id}: {error}")
                        continue

                if os.path.exists(out_path):
                    ok = self.upload_image(out_path, metadata)
                    if ok:
                        uploaded.append({"asset_id": asset_id, "path": out_path})
                    else:
                        logging.error(f"Failed to upload {out_path} for {asset_id}")

        logging.info(f"Uploaded {len(uploaded)} cropped images to Meural")
        return uploaded
