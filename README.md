# tadreb.live Video Uploader

A desktop application for Windows and Ubuntu to upload videos to the tadreb.live PeerTube instance.

## Features

*   Select video files from the local hard drive.
*   Connect to a specified PeerTube instance and authenticate.
*   Fetch and choose from your available PeerTube channels.
*   Enter a title for the video.
*   Add videos to an upload queue.
*   Automatic sequential uploading of videos from the queue.
*   Progress display for ongoing uploads.
*   Logging of application activity and upload status.

## Technology Stack

*   Python 3
*   PyQt5 for the graphical user interface.
*   Requests library for HTTP communication with the PeerTube API.
*   PyInstaller for packaging into standalone executables.

## Project Structure

```
peertube_uploader/
├── main.py               # Main application entry point
├── gui/
│   ├── main_window.py    # PyQt main window class and GUI logic
│   └── widgets/          # Custom GUI widgets (if any, currently empty)
├── api/
│   └── peertube_client.py # Class for PeerTube API interactions
├── core/
│   └── queue_manager.py  # Upload queue logic and task management
├── config/               # Configuration handling (if any persisted, currently empty)
├── assets/               # Icons, etc. (currently empty)
└── utils/                # Utility functions (currently empty)
build.sh                  # Script to build the application using PyInstaller (for Linux)
requirements.txt          # Python dependencies
README.md                 # This file (specific to the uploader app)
.gitignore                # Specifies intentionally untracked files
```
*(Other files in the root like LICENSE, CHANGELOG.md etc. might be from the original PeerTube project if this uploader is built within its structure)*

## Setup and Running from Source

1.  **Clone the repository (or ensure you have the `peertube_uploader` directory and other necessary files like `build.sh`, `requirements.txt`).**

2.  **Ensure Python 3 is installed.**
    You can download it from [python.org](https://www.python.org/).

3.  **Configure Credentials (Important!):**
    *   Open the file `peertube_uploader/config/settings.py`.
    *   Update the following lines with your PeerTube instance username and password:
        ```python
        PEERTUBE_USERNAME = "your_username"  # Replace with your actual username
        PEERTUBE_PASSWORD = "your_password"  # Replace with your actual password
        ```
    *   The instance URL is pre-configured to `https://store.tadreb.live`.
    *   **Security Warning:** Storing plaintext passwords in configuration files is a security risk. For production or shared environments, consider more secure methods (not yet implemented in this version).

4.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv venv_uploader
    source venv_uploader/bin/activate  # On Windows: venv_uploader\Scripts\activate
    ```

5.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

6.  **Run the application:**
    ```bash
    python peertube_uploader/main.py
    ```
    The application will attempt to connect automatically on startup using the credentials from `settings.py`.

## Building Standalone Executables

PyInstaller is used to create standalone executables for "tadreb.live Video Uploader". The necessary Python packages (PyQt5, requests) will be bundled.

### For Linux:

1.  **Ensure PyInstaller is installed** (it's in `requirements.txt`).
2.  **Make the build script executable:**
    ```bash
    chmod +x build.sh
    ```
3.  **Run the build script from the project root directory:**
    ```bash
    ./build.sh
    ```
    The executable will be located in the `dist/PeerTubeUploader/` directory (e.g., `dist/PeerTubeUploader/PeerTubeUploader`).

    **Note on Debian (.deb) Packaging:**
    The `build.sh` script also contains *illustrative manual steps* for creating a `.deb` package after the PyInstaller build. This involves creating a specific directory structure, a `.desktop` file for application launchers, and a `DEBIAN/control` file. For robust Debian packaging, consider using tools like `fpm` or `debhelper`. The script lists common Qt5 dependencies that might be needed for the control file.

### For Windows:

1.  **Ensure Python 3, PyQt5, Requests, and PyInstaller are installed** in your Windows environment.
    ```bash
    pip install pyqt5 requests pyinstaller
    ```
2.  **Open a Command Prompt or PowerShell in the project root directory.**
3.  **Run the PyInstaller command directly** (you can adapt it from the `build.sh` script, ensuring paths are correct for Windows):
    ```powershell
    pyinstaller --name "PeerTubeUploader" --onefile --windowed --distpath "dist" --workpath "build_pyinstaller" peertube_uploader/main.py
    ```
    (Add `--icon="path\to\your\icon.ico"` if you have an icon file for the executable).
    The executable (`PeerTubeUploader.exe`) will be located in the `dist\` directory.

4.  **Creating a Windows Installer (Optional):**
    To create a user-friendly Windows installer (e.g., `.msi` or a setup wizard), you can use tools like:
    *   **Inno Setup** (free)
    *   **NSIS (Nullsoft Scriptable Install System)** (free)
    These tools would take the `.exe` file and other assets generated by PyInstaller to create the installer package.

## Usage

1.  **Ensure credentials are set** in `peertube_uploader/config/settings.py` as described in the "Setup" section.
2.  Launch the application (either from source or the built executable).
3.  The application will automatically attempt to connect to the pre-configured instance (`https://store.tadreb.live`) using the credentials from `settings.py`.
    *   Observe the status label at the top for connection status (e.g., "Connecting...", "Connected as: username", "Connection Failed").
4.  If the connection is successful, your channels will be loaded into the "Channel" dropdown.
    *   **Regular Users:** Will see only their own channels.
    *   **Administrators/Moderators:** Will see all channels on the instance. Channels not owned by them will be indicated with an "(Owner: <owner_username>)" suffix.
6.  Click "Browse" to select a video file you want to upload.
6.  Select the desired channel from the dropdown (this will enable the "Add to Upload Queue" button).
7.  Enter a title for your video (must be between 3 and 120 characters).
8.  Click "Add to Upload Queue". The video will be added to the queue list.
9.  The application will automatically start processing the queue. Monitor progress and status in the queue list and the logs section.
10. You can remove tasks using "Remove Selected Task" or clear finished ones with "Clear Completed Tasks".

## Important Note on Uploading to Any Channel

The initial requirement stated: "The user should be able to upload videos to any channel, even if they do not own it."

**Behavior for Admin/Moderator Roles:**
*   If the authenticated user is an **Administrator** or **Moderator** on the PeerTube instance (as determined by their role ID), the "Channel" dropdown will list **all channels** available on that instance.
*   For channels not owned by the admin/moderator, the display will typically include "(Owner: <owner_username>)" for clarity.

**Behavior for Regular Users:**
*   If the authenticated user is a **regular user**, the "Channel" dropdown will only list channels **owned by that user**.

**Important Note on Upload Permissions:**
*   Listing a channel in the dropdown (especially for Admins/Moderators viewing channels they don't own) **does not automatically guarantee permission to upload to it.**
*   The actual ability to upload to a selected channel is determined by the PeerTube server based on the authenticated user's rights for that specific channel.
*   If an Admin or Moderator attempts to upload to a channel they can see but do not have explicit upload permissions for (as configured on the PeerTube server), the upload attempt will likely fail with an error from the server (e.g., a 403 Forbidden error). The application will report this failure.
*   Standard PeerTube permissions typically restrict direct uploads to channels a user owns or explicitly manages. Uploading to *any* arbitrary channel usually requires specific server-side configurations or permissions that are not standard for all moderator roles across all instances.

## Future Improvements

*   Persistent configuration for instance URL and potentially (securely stored) OAuth tokens to avoid re-login.
*   More detailed error reporting and options for retrying failed uploads.
*   Ability to edit more video metadata (description, tags, privacy settings, etc.) directly in the GUI before adding to the queue.
*   Thumbnail selection/preview for videos.
*   More robust and automated Debian/Windows installer creation as part of the build process (e.g., using `fpm` or Inno Setup scripting).
*   Internationalization (i18n) support for the GUI.
*   Option to specify video description, tags, and privacy settings per video.
*   A settings dialog for application-specific configurations.
```
