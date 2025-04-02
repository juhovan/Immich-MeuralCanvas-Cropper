/**
 * meural-service.js - Meural Canvas Integration
 *
 * Handles Meural Canvas preview functionality
 */

// Cache for Meural devices
let meuralDevices = [];
let meuralPreviewInProgress = false;

/**
 * Get available Meural devices
 */
async function getMeuralDevices() {
    if (meuralDevices.length > 0) {
        return meuralDevices;
    }

    try {
        const response = await fetch('/meural/devices');
        const data = await response.json();

        if (data.devices && Array.isArray(data.devices)) {
            meuralDevices = data.devices;
            return data.devices;
        }
        return [];
    } catch (error) {
        console.error('Error fetching Meural devices:', error);
        return [];
    }
}

/**
 * Preview image on a Meural Canvas
 */
async function previewOnMeural(deviceIp, identifier, orientation, useTempCrop = false, cropData = null) {
    // Prevent multiple preview operations
    if (meuralPreviewInProgress) {
        console.log("Preview blocked - operation in progress");
        return { success: false, message: "Preview already in progress" };
    }

    meuralPreviewInProgress = true;

    try {
        const payload = {
            device_ip: deviceIp,
            identifier: identifier,
            orientation: orientation,
            use_temp: useTempCrop
        };

        // Add crop data if using temp crop
        if (useTempCrop && cropData) {
            payload.crop = cropData;
        }

        const response = await fetch('/meural/preview', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Preview sent to Meural ${deviceIp}`, {
                message: data.message,
                previewDuration: data.preview_duration,
                timestamp: new Date().toISOString()
            });
        } else {
            console.error('Error sending preview to Meural:', data.message);
        }

        return data;
    } catch (error) {
        console.error('Error previewing on Meural:', error);
        return { success: false, message: error.toString() };
    } finally {
        meuralPreviewInProgress = false;
    }
}

/**
 * Show Meural device selection dialog
 */
function showMeuralDeviceDialog(callback) {
    // Get the devices first
    getMeuralDevices().then(devices => {
        if (devices.length === 0) {
            alert('No Meural devices configured. Please add devices in config.yaml.');
            return;
        }

        // Create a simple dialog
        const dialog = document.createElement('div');
        dialog.className = 'meural-device-dialog';
        dialog.innerHTML = `
            <div class="meural-device-dialog-content">
                <h3>Select Meural Device</h3>
                <p>Choose a device to preview your image:</p>
                <div class="device-list">
                    ${devices.map(device => `
                        <div class="device-item" data-ip="${device.ip}">
                            <i class="fas fa-tv"></i>
                            <span>${device.name} (${device.ip})</span>
                        </div>
                    `).join('')}
                </div>
                <div class="dialog-buttons">
                    <button class="btn btn-meural btn-secondary" id="cancel-meural">Cancel</button>
                </div>
            </div>
        `;

        // Add to document
        document.body.appendChild(dialog);

        // Add click handlers
        dialog.querySelectorAll('.device-item').forEach(item => {
            item.addEventListener('click', () => {
                const deviceIp = item.getAttribute('data-ip');
                document.body.removeChild(dialog);
                callback(deviceIp);
            });
        });

        // Cancel button
        document.getElementById('cancel-meural').addEventListener('click', () => {
            document.body.removeChild(dialog);
        });
    });
}

/**
 * Preview current image on Meural Canvas
 */
function previewCurrentOnMeural() {
    const currentImage = window.APP_STATE.currentImage;
    const currentStage = window.APP_STATE.currentStage;

    if (!currentImage) {
        alert('No image selected.');
        return;
    }

    if (window.APP_STATE.syncing) {
        alert('Cannot preview while syncing.');
        return;
    }

    // Get the identifier (prefer asset_id if available)
    const identifier = currentImage.asset_id || currentImage.filename;

    // Determine which orientation to preview based on current stage
    const usePortrait = currentStage === 1 || (currentStage === 3 && window.APP_STATE.portraitCrop.width > 0);
    const useLandscape = currentStage === 2 || (currentStage === 3 && window.APP_STATE.landscapeCrop.width > 0);

    if (!usePortrait && !useLandscape) {
        alert('No crop available to preview.');
        return;
    }

    const orientation = usePortrait ? 'portrait' : 'landscape';

    // For current editing stage, we need to create a temporary crop
    const useTempCrop = currentStage < 3;

    // IMPORTANT: Make sure we're getting the currently selected crop data
    // based on what's shown in the crop rectangle
    let cropData;

    if (currentStage < 3 && window.ELEMENTS.cropRectangleEl) {
        // Get current crop position and dimensions directly from the DOM element
        const cropRect = window.ELEMENTS.cropRectangleEl;
        const imgRect = window.ELEMENTS.currentImageEl;

        // Get container bounds
        const containerRect = window.ELEMENTS.editorContainerEl.getBoundingClientRect();
        const imgBounds = imgRect.getBoundingClientRect();

        // Calculate the image position relative to the container
        const imgLeft = imgBounds.left - containerRect.left;
        const imgTop = imgBounds.top - containerRect.top;

        // Get crop rectangle position and size
        const cropLeft = parseInt(cropRect.style.left) || 0;
        const cropTop = parseInt(cropRect.style.top) || 0;
        const cropWidth = parseInt(cropRect.style.width) || 0;
        const cropHeight = parseInt(cropRect.style.height) || 0;

        // Calculate crop position relative to the image
        const x = cropLeft - imgLeft;
        const y = cropTop - imgTop;

        // Calculate scale factors to convert from display size to original image size
        const scaleX = imgRect.naturalWidth / imgRect.offsetWidth;
        const scaleY = imgRect.naturalHeight / imgRect.offsetHeight;

        // Scale the crop coordinates to match the original image dimensions
        cropData = {
            x: Math.floor(x * scaleX),
            y: Math.floor(y * scaleY),
            width: Math.floor(cropWidth * scaleX),
            height: Math.floor(cropHeight * scaleY)
        };

        console.log('Current crop rectangle coordinates:', {
            cropRect: { left: cropLeft, top: cropTop, width: cropWidth, height: cropHeight },
            imgBounds: { left: imgLeft, top: imgTop, width: imgBounds.width, height: imgBounds.height },
            scaledCrop: cropData
        });
    } else {
        // Use stored crop data
        cropData = usePortrait ? window.APP_STATE.portraitCrop : window.APP_STATE.landscapeCrop;

        // If we have stored crop data for the current stage, we need to scale it
        // from display coordinates to original image coordinates
        if (cropData && cropData.width > 0) {
            const imgRect = window.ELEMENTS.currentImageEl;

            // Calculate scale factors
            const scaleX = imgRect.naturalWidth / imgRect.offsetWidth;
            const scaleY = imgRect.naturalHeight / imgRect.offsetHeight;

            // Scale the crop coordinates
            cropData = {
                x: Math.floor(cropData.x * scaleX),
                y: Math.floor(cropData.y * scaleY),
                width: Math.floor(cropData.width * scaleX),
                height: Math.floor(cropData.height * scaleY)
            };
        }
    }

    // Add explicit logging to show what we're sending
    console.log('Sending to Meural API:', {
        identifier: identifier,
        orientation: orientation,
        useTempCrop: useTempCrop,
        cropData: cropData
    });

    // Show device selection dialog
    showMeuralDeviceDialog(deviceIp => {
        // Show loading indicator
        const previewBtn = window.ELEMENTS.btnMeuralPreviewEl;
        const originalText = previewBtn.innerHTML;
        previewBtn.disabled = true;
        previewBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Previewing...';

        // Send preview to selected device
        previewOnMeural(deviceIp, identifier, orientation, useTempCrop, cropData)
            .then(result => {
                if (!result.success) {
                    alert(`Error: ${result.message}`);
                }
            })
            .catch(error => {
                alert(`Error: ${error.toString()}`);
            })
            .finally(() => {
                // Reset button
                previewBtn.disabled = false;
                previewBtn.innerHTML = originalText;
            });
    });
}