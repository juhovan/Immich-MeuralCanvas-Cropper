import os
import base64
import logging
import time
from typing import List, Dict, Any, Optional
import requests
from PIL import Image
import io
from utils.file_handler import get_filename_from_asset_id


class MeuralHandler:
    """Handler for Meural Canvas devices to preview images directly."""

    def __init__(self, devices_config):
        """Initialize the Meural handler with device configurations.

        Args:
            devices_config (list): List of device configurations from config.yaml
        """
        self.devices = devices_config
        self._validate_config()
        logging.info(f"Initialized MeuralHandler with {len(self.devices)} devices")

    def _validate_config(self):
        """Validate device configurations."""
        if not self.devices:
            logging.warning("No Meural devices configured")
            return

        for idx, device in enumerate(self.devices):
            if not device.get("ip"):
                logging.warning(f"Device {idx+1} missing IP address")
            if not device.get("name"):
                logging.warning(f"Device at {device.get('ip', 'unknown')} missing name")
            device["preview_duration"] = device.get("preview_duration", 30)

    def get_device_list(self):
        """Get list of configured Meural devices.

        Returns:
            list: List of device dictionaries with name and ip
        """
        return [{"name": d["name"], "ip": d["ip"]} for d in self.devices]

    def _set_preview_duration(self, device_ip: str, duration: int) -> bool:
        """
        Set the preview duration on the Meural Canvas device.

        Args:
            device_ip (str): IP address of the device
            duration (int): Duration in seconds

        Returns:
            bool: Success status
        """
        try:
            # Use control_command to set the preview duration
            url = f"http://{device_ip}/remote/control_command/set_key/"
            params = {"key": "previewDuration", "value": str(duration)}
            logging.info(
                f"Setting preview duration to {duration}s with URL: {url}, params: {params}"
            )
            response = requests.get(url, params=params, timeout=5)

            logging.info(
                f"Preview duration response status: {response.status_code}, content: {response.text[:200]}"
            )

            if response.status_code == 200:
                try:
                    json_response = response.json()
                    if json_response.get("status") == "pass":
                        logging.info(
                            f"Successfully set preview duration to {duration}s on {device_ip}"
                        )
                        return True
                except:
                    pass

                # If we got here, we got a 200 OK but couldn't parse JSON or it wasn't a success
                logging.info(f"Got 200 OK for preview duration but status unclear")
                return True
            else:
                logging.error(f"Failed to set preview duration: {response.status_code}")
                return False

        except Exception as e:
            logging.error(f"Error setting preview duration: {e}")
            return False

    def crop_image(self, image_path: str, crop_data: Dict[str, int]) -> Optional[bytes]:
        """
        Crop an image according to the specified crop data.

        Args:
            image_path (str): Path to the image file
            crop_data (dict): Dictionary with x, y, width, height keys

        Returns:
            bytes: Cropped image data as bytes, or None if error
        """
        try:
            if not os.path.exists(image_path):
                logging.error(f"Image not found: {image_path}")
                return None

            # Open the image
            img = Image.open(image_path)

            # Log the original image size
            logging.info(f"Original image size: {img.width}x{img.height}")

            # Extract crop coordinates
            x = crop_data.get("x", 0)
            y = crop_data.get("y", 0)
            width = crop_data.get("width", img.width)
            height = crop_data.get("height", img.height)

            # Log the raw crop data
            logging.info(f"Raw crop data: x={x}, y={y}, width={width}, height={height}")

            # Ensure crop coordinates are within image bounds
            x = max(0, min(x, img.width - 1))
            y = max(0, min(y, img.height - 1))
            width = min(width, img.width - x)
            height = min(height, img.height - y)

            # Log the corrected crop dimensions
            logging.info(f"Adjusted crop: x={x}, y={y}, width={width}, height={height}")

            # Crop the image
            cropped_img = img.crop((x, y, x + width, y + height))
            logging.info(
                f"Cropped image size: {cropped_img.width}x{cropped_img.height}"
            )

            # Save the cropped image to a temporary file for debugging (optional)
            debug_path = os.path.join("/tmp", f"meural_debug_{int(time.time())}.jpg")
            cropped_img.save(debug_path)
            logging.info(f"Saved debug image to {debug_path}")

            # Save to a bytes buffer
            buffer = io.BytesIO()
            cropped_img.save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            return buffer.getvalue()

        except Exception as e:
            logging.error(f"Error cropping image: {e}")
            return None

    def preview_image(
        self,
        device_ip: str,
        image_path: str,
        crop_data: Optional[Dict[str, int]] = None,
    ) -> dict:
        """
        Send an image to a Meural Canvas device for preview.
        Uses multipart/form-data with key 'photo'

        Args:
            device_ip (str): IP address of the target Meural device
            image_path (str): Path to the image file to preview
            crop_data (dict): Optional crop data with x, y, width, height keys

        Returns:
            dict: Response with success status and message
        """
        try:
            if not os.path.exists(image_path):
                return {"success": False, "message": f"Image not found: {image_path}"}

            # Find device configuration
            device_config = next(
                (d for d in self.devices if d["ip"] == device_ip), None
            )
            if not device_config:
                return {
                    "success": False,
                    "message": f"Device not found with IP: {device_ip}",
                }

            # First set the preview duration
            preview_duration = device_config.get("preview_duration", 30)
            self._set_preview_duration(device_ip, preview_duration)

            # Determine content type based on file extension
            extension = os.path.splitext(image_path)[1].lower()
            if extension in [".jpg", ".jpeg"]:
                content_type = "image/jpeg"
            elif extension in [".png"]:
                content_type = "image/png"
            else:
                content_type = "image/jpeg"  # Default to JPEG

            logging.info(f"Image path: {image_path}, Content type: {content_type}")

            # Process the image based on whether crop data was provided
            if crop_data and all(k in crop_data for k in ["x", "y", "width", "height"]):
                logging.info(f"Using crop data: {crop_data}")
                img_data = self.crop_image(image_path, crop_data)
                if not img_data:
                    return {"success": False, "message": "Failed to crop image"}
                logging.info(f"Cropped image size: {len(img_data)} bytes")
            else:
                logging.info("No valid crop data provided, using original image")
                with open(image_path, "rb") as img_file:
                    img_data = img_file.read()
                logging.info(f"Original image size: {len(img_data)} bytes")

            # Create multipart form data with key 'photo'
            files = {"photo": (os.path.basename(image_path), img_data, content_type)}

            # Send the POST request
            url = f"http://{device_ip}/remote/postcard"
            logging.info(
                f"Sending POST request to {url} with photo data of size {len(img_data)} bytes"
            )

            response = requests.post(url, files=files, timeout=30)

            logging.info(f"Response status: {response.status_code}")
            if response.text:
                logging.info(f"Response content: {response.text[:200]}")

            # Parse JSON response
            try:
                json_response = response.json()
                if json_response.get("status") == "pass":
                    return {
                        "success": True,
                        "message": f"Image previewed on {device_config['name']} ({device_ip})",
                        "preview_duration": preview_duration,
                    }
                else:
                    return {
                        "success": False,
                        "message": f"API error: {json_response.get('response', 'Unknown error')}",
                    }
            except Exception as e:
                logging.error(f"Error parsing response: {e}")

                # If we got a 200 OK but couldn't parse the JSON, assume it worked
                if response.status_code == 200:
                    return {
                        "success": True,
                        "message": f"Image sent to {device_config['name']} ({device_ip}) with status code 200",
                        "preview_duration": preview_duration,
                    }

            # If we get here, all methods failed
            return {
                "success": False,
                "message": "Failed to preview image. Check logs for details.",
            }

        except Exception as e:
            logging.error(f"Error in preview_image: {str(e)}")
            return {"success": False, "message": f"Error: {str(e)}"}

    def preview_image_by_asset_id(
        self,
        device_ip: str,
        asset_id: str,
        orientation: Optional[str] = None,
        crop_data: Optional[Dict[str, int]] = None,
    ) -> dict:
        """
        Send an image to a Meural Canvas device for preview using asset ID.

        Args:
            device_ip (str): IP address of the target Meural device
            asset_id (str): Immich asset ID
            orientation (str, optional): Either 'portrait' or 'landscape'
            crop_data (dict, optional): Crop data with x, y, width, height keys

        Returns:
            dict: Response with success status and message
        """
        try:
            # If orientation is provided, look for the processed image
            if orientation in ["portrait", "landscape"]:
                output_filename = f"{asset_id}_{orientation}.jpg"
                output_path = os.path.join(
                    "/tmp/meural-cropper/output", orientation, output_filename
                )

                if os.path.exists(output_path):
                    # Use the processed image
                    return self.preview_image(device_ip, output_path)

            # If no orientation provided or processed image not found, use original with crop
            filename = get_filename_from_asset_id(asset_id)
            if not filename:
                return {
                    "success": False,
                    "message": f"No file found for asset ID: {asset_id}",
                }

            input_path = os.path.join("/tmp/meural-cropper/input", filename)
            if not os.path.exists(input_path):
                return {
                    "success": False,
                    "message": f"Original file not found: {input_path}",
                }

            # Send preview with crop data if provided
            return self.preview_image(device_ip, input_path, crop_data)

        except Exception as e:
            logging.error(f"Error in preview_image_by_asset_id: {str(e)}")
            return {"success": False, "message": f"Error: {str(e)}"}
