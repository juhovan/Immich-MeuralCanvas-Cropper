import logging
import time
import requests
import urllib.request

import config
from datetime import datetime
# New imports for comparison helpers
from typing import Set, Dict, Any, Optional, List
import re
from utils.file_handler import get_asset_metadata
import boto3
# New imports for sync helpers
import os
import json
from utils.immich_handler import ImmichHandler

class MeuralUpload:
    def __init__(self, meural_username, meural_password):
        self.username = meural_username
        self.password = meural_password
        self.token = None
        self.token_time = None
        self.base_url = "https://api.meural.com/v0"
        self.authenticate()
        self.compare_playlist_with_immich()
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
                self.token = response["AuthenticationResult"]["AccessToken"]
                self.token_time = time.time()
                return self.token
            else:
                logging.error("Authentication failed: No AuthenticationResult in response.")
                return None
        except client.exceptions.NotAuthorizedException as auth_err:
            logging.error(f"Not authorized: {auth_err}")
            return None
        except client.exceptions.UserNotFoundException as user_err:
            logging.error(f"User not found: {user_err}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error during authentication: {e}")
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

    def _set_image_metadata(self, image_id, name, description, medium, year):
        if (time.time() - self.token_time) > 300:
            self.authenticate()

        headers = {"Authorization": "Token " + self.token}
        data = {
            "name": name,
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
        r, _ = self._json_request_with_retry(
            "POST",
            f"{self.base_url}/galleries/{playlist_id}/items/{image_id}",
            headers=headers,
        )
        if r is None:
            logging.error(f"Error adding image {image_id} to playlist {playlist_id}: invalid/empty JSON response")
            return False

        logging.info(f"Meural add to playlist response: {r}")
        if "error" in r:
            logging.error(f"Error adding image {image_id} to playlist {playlist_id}: {r['error']}")
            return False
        return True

    def _format_exif_description(self, exif: Dict[str, Any]) -> str:
        """Format EXIF data into a description string."""
        make = exif.get("make") or ""
        model = exif.get("model") or ""
        lens_model = exif.get("lensModel") or ""
        exposure_time = "" if exif.get("exposureTime") is None else f"{exif.get('exposureTime')}s "
        f_number = "" if exif.get("fNumber") is None else f"f/{exif.get('fNumber')} "
        iso = "" if exif.get("iso") is None else f"ISO{exif.get('iso')} "
        focal_length = "" if exif.get("focalLength") is None else f"{exif.get('focalLength')}mm"
        
        return f"{make} {model} {lens_model} {exposure_time}{f_number}{iso}{focal_length}".strip()

    def _metadata_changed(self, current_metadata: Dict[str, Any], current_description: str) -> bool:
        """Compare relevant metadata fields directly with what's in Meural description."""
        try:
            expected_desc = self._format_exif_description(current_metadata.get("exif", {}))
            actual_desc = current_description.rsplit('\n', 1)[0].strip()
            return expected_desc != actual_desc
        except Exception as e:
            logging.error(f"Error comparing metadata: {e}")
            return False

    def upload_image(self, image_path, metadata):
        image_id = self._upload_image_data(image_path)
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

        # Use asset_id instead of original_filename in the last line of the description
        # This is our unique identifier for matching between Immich and Meural
        asset_id = metadata.get("asset_id", "")
        if not asset_id:
            # Fallback to original_filename only if we must
            asset_id = metadata["original_filename"]

        description = f"{self._format_exif_description(exif)}\n{asset_id}".strip()

        set_image_metdata = self._set_image_metadata(image_id, name, description, medium, year)
        add_to_playlist = self._add_to_playlist(image_id, config.MEURAL_PLAYLIST_ID)
        if not set_image_metdata or not add_to_playlist:
            logging.error(f"Failed to set metadata or add image {image_id} to playlist.")
            return False
        logging.info(f"Image {image_id} uploaded and metadata set successfully.")
        return True

    # Helper: list all items in a Meural playlist (returns list of dicts)
    def _list_playlist_items(self, playlist_id: str, per_page: int = 100) -> List[Dict[str, Any]]:
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
                meta = data.get("meta", {})
                if total_pages is None:
                    total_pages = meta.get("total_pages") or data.get("total_pages") or None
                if total_pages:
                    if page >= int(total_pages):
                        break
                else:
                    if len(items) < per_page:
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

    # New method: compare Meural playlist contents with Immich output album
    def compare_playlist_with_immich(
        self,
        playlist_id: Optional[str] = None,
        immich_album_id: Optional[str] = None,
        per_page: int = 100
    ) -> Dict[str, Any]:
        """
        Fetch current Meural playlist items and compare to Immich output album.

        Uses the last non-empty line of Meural item's description (which we set to original_filename)
        and compares against original filenames resolved from Immich processed assets via local metadata.

        Returns a dict with:
          - in_meural: sorted list of original filenames found on Meural
          - in_immich: sorted list of original filenames found in Immich album (via metadata)
          - missing_on_meural: in Immich but not on Meural
          - only_on_meural: on Meural but not in Immich
          - counts: summary counts
        """
        playlist_id = playlist_id or getattr(config, "MEURAL_PLAYLIST_ID", None)
        immich_album_id = immich_album_id or getattr(config, "IMMICH_OUTPUT_ALBUM_ID", None)

        if not playlist_id:
            logging.error("Meural playlist ID not provided")
            return {"error": "Meural playlist ID not provided"}

        if not immich_album_id:
            logging.error("Immich output album ID not provided")
            return {"error": "Immich output album ID not provided"}

        # Ensure token is fresh
        try:
            if not self.token_time or (time.time() - self.token_time) > 300:
                self.authenticate()
        except Exception as e:
            logging.error(f"Failed to authenticate with Meural API: {e}")
            return {"error": f"Meural auth failed: {e}"}

        # 1) Collect asset IDs (not original filenames) present on Meural (from description last line)
        meural_asset_ids: Set[str] = set()
        try:
            headers = {"Authorization": f"Token {self.token}"}
            page = 1
            total_pages = None
            while True:
                url = f"{self.base_url}/galleries/{playlist_id}/items"
                params = {"page": page, "per_page": per_page}
                data, resp = self._json_request_with_retry("GET", url, headers=headers, params=params, timeout=15)
                if data is None:
                    logging.error(f"Failed to fetch Meural playlist items (page {page}) due to invalid/empty JSON")
                    break
                if resp is not None and not resp.ok:
                    logging.error(f"Failed to fetch Meural playlist items: {resp.status_code} {resp.text[:200]}")
                    break

                items = data.get("data")
                if items is None:
                    # Some responses might return the list directly
                    if isinstance(data, list):
                        items = data
                    else:
                        items = data.get("items", [])

                if not items:
                    break

                for item in items:
                    desc = (item or {}).get("description") or ""
                    lines = [ln.strip() for ln in desc.splitlines() if ln.strip()]
                    if len(lines) >= 2:  # We expect at least signature and asset_id
                        asset_id = lines[-1]
                        meural_asset_ids.add(asset_id)

                # Pagination handling
                meta = data.get("meta", {})
                if total_pages is None:
                    total_pages = meta.get("total_pages") or data.get("total_pages") or None

                # Fallback: stop when we got fewer than per_page items
                if total_pages:
                    if page >= int(total_pages):
                        break
                else:
                    if len(items) < per_page:
                        break

                page += 1
        except Exception as e:
            logging.error(f"Error while fetching Meural playlist items: {e}")

        # 2) Collect asset IDs represented in Immich output album
        immich_asset_ids: Set[str] = set()
        try:
            immich_base = config.IMMICH_URL.rstrip("/")
            headers = {"x-api-key": config.IMMICH_API_KEY, "Accept": "application/json"}
            data, resp = self._json_request_with_retry("GET", f"{immich_base}/api/albums/{immich_album_id}", headers=headers, timeout=20)
            if data is None:
                logging.error("Failed to fetch Immich album assets due to invalid/empty JSON")
                return {"error": "Immich fetch failed: invalid/empty JSON"}
            if resp is not None:
                resp.raise_for_status()
            album = data
            assets: List[Dict[str, Any]] = album.get("assets", []) or []

            for asset in assets:
                processed_name = asset.get("originalFileName") or ""
                # Expect pattern: <assetId>_<orientation>.<ext>
                if "_" not in processed_name:
                    continue
                base_asset_id = processed_name.split("_", 1)[0]
                immich_asset_ids.add(base_asset_id)

        except Exception as e:
            logging.error(f"Error while fetching Immich album assets: {e}")
            return {"error": f"Immich fetch failed: {e}"}

        # 3) Compare sets
        missing_on_meural = sorted(list(immich_asset_ids - meural_asset_ids))
        only_on_meural = sorted(list(meural_asset_ids - immich_asset_ids))
        in_both = sorted(list(meural_asset_ids & immich_asset_ids))

        result = {
            "in_meural": sorted(list(meural_asset_ids)),
            "in_immich": sorted(list(immich_asset_ids)),
            "missing_on_meural": missing_on_meural,
            "only_on_meural": only_on_meural,
            "in_both": in_both,
            "counts": {
                "meural": len(meural_asset_ids),
                "immich": len(immich_asset_ids),
                "missing_on_meural": len(missing_on_meural),
                "only_on_meural": len(only_on_meural),
                "in_both": len(in_both),
            },
        }

        logging.info(
            "Meural vs Immich comparison: Meural=%d Immich=%d MissingOnMeural=%d OnlyOnMeural=%d",
            result["counts"]["meural"],
            result["counts"]["immich"],
            result["counts"]["missing_on_meural"],
            result["counts"]["only_on_meural"],
        )
        return result

    # New method: sync Meural playlist to match Immich output album (Immich is source of truth)
    def sync_playlist_with_immich(
        self,
        playlist_id: Optional[str] = None,
        immich_album_id: Optional[str] = None,
        per_page: int = 100
    ) -> Dict[str, Any]:
        """
        Make Meural playlist match Immich output album:
          - Add missing images (by original filename) to Meural from local processed outputs
          - Remove images from the playlist that are not present in Immich (by original filename)
          - If processed files are not found locally, try to locate processed assets in Immich output
            album and download them before uploading to Meural.
        """
        playlist_id = playlist_id or getattr(config, "MEURAL_PLAYLIST_ID", None)
        immich_album_id = immich_album_id or getattr(config, "IMMICH_OUTPUT_ALBUM_ID", None)

        if not playlist_id or not immich_album_id:
            return {"error": "playlist_id and immich_album_id are required"}

        comparison = self.compare_playlist_with_immich(playlist_id, immich_album_id, per_page=per_page)
        if "error" in comparison:
            return comparison

        immich_names: Set[str] = set(comparison.get("in_immich", []))
        meural_names: Set[str] = set(comparison.get("in_meural", []))

        # Build mapping of asset_id -> details from Meural
        items = self._list_playlist_items(playlist_id, per_page=per_page)
        meural_map: Dict[str, Dict[str, Any]] = {}
        for it in items:
            desc = (it or {}).get("description") or ""
            lines = [ln.strip() for ln in desc.splitlines() if ln.strip()]
            if lines:  # At least one line with asset_id
                asset_id = lines[-1]
                meural_map[asset_id] = {
                    "item_id": it.get("id"),
                    "description": '\n'.join(lines[:-1])  # All lines except asset_id
                }

        to_remove = sorted(list(meural_names - immich_names))
        to_add = sorted(list(immich_names - meural_names))
        to_update = []

        # Check for metadata changes in existing items by comparing descriptions
        for asset_id in immich_names & meural_names:
            try:
                metadata = get_asset_metadata(asset_id)
                current_desc = meural_map.get(asset_id, {}).get("description", "")
                
                if self._metadata_changed(metadata, current_desc):
                    logging.info(f"Detected metadata changes for asset {asset_id}")
                    to_update.append((asset_id, metadata))
            except Exception as e:
                logging.error(f"Error checking metadata changes for {asset_id}: {e}")

        removed = []
        added = []
        errors = []

        # Remove out-of-truth items from Meural
        for asset_id in to_remove:
            for item_id in meural_map.get(asset_id, []):
                ok = self._remove_from_playlist(item_id, playlist_id)
                if ok:
                    removed.append({"item_id": item_id})
                else:
                    errors.append(f"Failed to remove item {item_id} for asset {asset_id}")

        # No need for a helper to find asset_id by original_filename
        # since we're using the asset_id directly from the last line

        immich = self._get_immich()

        # Add missing items to Meural
        for asset_id in to_add:  # These are now asset IDs, not original filenames
            # Get metadata to build Meural description
            metadata = get_asset_metadata(asset_id)

            found_any = False
            for orientation in ["portrait", "landscape"]:
                # Look for local processed file first
                out_name = f"{asset_id}_{orientation}.jpg"
                out_path = os.path.join(config.OUTPUT_FOLDER, orientation, out_name)

                if not os.path.exists(out_path):
                    # Try to locate existing processed asset in Immich output album
                    try:
                        existing = immich._find_existing_processed_asset(asset_id, orientation, immich.output_album_id)
                        proc_asset_id = existing["id"] if existing else None
                        if proc_asset_id:
                            # Download processed asset into the correct orientation folder
                            os.makedirs(os.path.join(config.OUTPUT_FOLDER, orientation), exist_ok=True)
                            ok, downloaded_path = immich.download_asset(proc_asset_id, os.path.join(config.OUTPUT_FOLDER, orientation))
                            if ok and downloaded_path and os.path.exists(downloaded_path):
                                out_path = downloaded_path
                                logging.info(f"Downloaded processed asset for {asset_id} ({orientation}) to {out_path}")
                    except Exception as e:
                        logging.error(f"Failed to find/download processed asset in Immich for {asset_id} ({orientation}): {e}")

                if os.path.exists(out_path):
                    ok = self.upload_image(out_path, metadata)
                    if ok:
                        added.append({"asset_id": asset_id, "path": out_path})
                        found_any = True
                    else:
                        errors.append(f"Failed to upload {out_path} for {asset_id}")

            if not found_any:
                errors.append(f"No processed files found (locally or in Immich) for asset {asset_id}")

        # Update changed metadata
        for asset_id, metadata in to_update:
            item_id = meural_map[asset_id]["item_id"]
            try:
                # Reuse same metadata formatting as upload
                exif = metadata["exif"]
                name = (exif.get("description") or metadata["original_filename"])
                medium = ((exif.get("city") or "") + " " +
                        (exif.get("state") or "") + " " +
                        (exif.get("country") or "")).strip()
                # ...existing date parsing code...
                
                description = f"{self._format_exif_description(exif)}\n{asset_id}".strip()
                
                if self._set_image_metadata(item_id, name, description, medium, year):
                    added.append({"asset_id": asset_id, "action": "metadata_updated"})
                else:
                    errors.append(f"Failed to update metadata for {asset_id}")
            except Exception as e:
                errors.append(f"Error updating metadata for {asset_id}: {e}")

        summary = {
            "success": len(errors) == 0,
            "removed_count": len(removed),
            "added_count": len(added),
            "removed": removed,
            "added": added,
            "errors": errors,
            "counts_before": comparison.get("counts", {}),
        }
        logging.info(f"Sync summary: added={summary['added_count']} removed={summary['removed_count']} errors={len(errors)}")
        if len(errors) > 0:
            logging.error(f"Errors during sync: {errors}")
        return summary
        return summary
