#!/bin/bash

# Ensure we are in the script's directory or a known base directory
# For simplicity, this script assumes it's run from the repository root.

APP_NAME="TadrebLiveVideoUploader" # Updated App Name
MAIN_SCRIPT="peertube_uploader/main.py"
DIST_PATH="dist"
BUILD_PATH="build_pyinstaller" # Renamed to avoid conflict with potential 'build' dir

echo "Starting PyInstaller build for $APP_NAME..."

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf "$DIST_PATH/$APP_NAME"
rm -rf "$BUILD_PATH"
rm -f "$APP_NAME.spec" # PyInstaller spec file

# Create a virtual environment for cleaner builds (optional but good practice)
# python -m venv .pyinstaller_venv
# source .pyinstaller_venv/bin/activate
# pip install -r requirements.txt # Assuming a requirements.txt exists or install PyQt5, requests

echo "Running PyInstaller..."
pyinstaller --name "$APP_NAME" \
            --onefile \
            --windowed \
            --icon="NONE" \
            --distpath "$DIST_PATH" \
            --workpath "$BUILD_PATH" \
            --add-data "peertube_uploader/assets:assets" \
            "$MAIN_SCRIPT"
            # --hidden-import "PyQt5.sip" \ # Sometimes needed for PyQt5
            # Add other hidden imports or data files if necessary

# Deactivate virtual environment if used
# deactivate
# rm -rf .pyinstaller_venv

if [ $? -eq 0 ]; then
    echo "Build successful! Executable is in $DIST_PATH/$APP_NAME/"
    echo "Note: For Windows, run the pyinstaller command in a Windows environment."
    echo "For creating a Debian package, further steps using tools like dpkg-deb or fpm would be needed after this."
else
    echo "PyInstaller build failed."
    exit 1
fi

# Placeholder for creating a Debian package (manual steps for now)
echo ""
echo "To create a Debian (.deb) package (manual steps example):"
echo "1. Create a directory structure for the package:"
echo "   mkdir -p ${APP_NAME}_debian/DEBIAN"
echo "   mkdir -p ${APP_NAME}_debian/usr/local/bin"
echo "   mkdir -p ${APP_NAME}_debian/usr/share/applications"
echo "   mkdir -p ${APP_NAME}_debian/usr/share/icons/hicolor/128x128/apps" # Example icon size
echo ""
echo "2. Copy the executable:"
echo "   cp \"$DIST_PATH/$APP_NAME/$APP_NAME\" \"${APP_NAME}_debian/usr/local/bin/\""
echo ""
echo "3. Create a .desktop file (e.g., ${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop):"
echo "   echo \"[Desktop Entry]\" > \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\""
echo "   echo \"Name=$APP_NAME\" >> \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\""
echo "   echo \"Exec=/usr/local/bin/$APP_NAME\" >> \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\""
echo "   echo \"Type=Application\" >> \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\""
echo "   echo \"Categories=Network;FileTransfer;Video;\" >> \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\""
echo "   # echo \"Icon=/usr/share/icons/hicolor/128x128/apps/$APP_NAME.png\" >> \"${APP_NAME}_debian/usr/share/applications/$APP_NAME.desktop\" # If you have an icon"
echo ""
echo "4. (Optional) Add an icon to ${APP_NAME}_debian/usr/share/icons/hicolor/128x128/apps/$APP_NAME.png"
echo ""
echo "5. Create the DEBIAN/control file (e.g., ${APP_NAME}_debian/DEBIAN/control):"
echo "   echo \"Package: $(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]')\" > \"${APP_NAME}_debian/DEBIAN/control\""
echo "   echo \"Version: 1.0.0\" >> \"${APP_NAME}_debian/DEBIAN/control\""
echo "   echo \"Section: utils\" >> \"${APP_NAME}_debian/DEBIAN/control\""
echo "   echo \"Priority: optional\" >> \"${APP_NAME}_debian/DEBIAN/control\""
echo "   echo \"Architecture: amd64\" >> \"${APP_NAME}_debian/DEBIAN/control\" # Adjust if needed"
echo "   echo \"Depends: libxkbcommon-x11-0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxcb-xinerama0, libxcb-xinput0, libfontconfig1, libgl1, libdbus-1-3\" >> \"${APP_NAME}_debian/DEBIAN/control\" # Common Qt5 deps, may vary"
echo "   echo \"Maintainer: Your Name <you@example.com>\" >> \"${APP_NAME}_debian/DEBIAN/control\""
echo "   echo \"Description: PeerTube Video Uploader application.\" >> \"${APP_NAME}_debian/DEBIAN/control\""
echo ""
echo "6. Set permissions (example):"
echo "   # sudo chown -R root:root ${APP_NAME}_debian" # May not be needed if building as root or in fakeroot
echo "   # chmod 755 ${APP_NAME}_debian/usr/local/bin/$APP_NAME"
echo ""
echo "7. Build the .deb package:"
echo "   dpkg-deb --build ${APP_NAME}_debian"
echo "   echo \"Debian package would be: ${APP_NAME}_debian.deb\""
echo ""
echo "These Debian packaging steps are illustrative. For a robust package, use tools like fpm or more detailed debhelper scripts."

exit 0
