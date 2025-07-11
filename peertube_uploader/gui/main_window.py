import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog,
                             QComboBox, QGroupBox, QTextEdit, QListWidget, QListWidgetItem,
                             QInputDialog, QMessageBox, QStatusBar, QProgressBar,
                             QSplitter, QFrame, QAbstractItemView, QTreeView, QListView, # Added QTreeView, QListView
                             QFileSystemModel, QStyle) # Added QFileSystemModel, QStyle
import datetime # For timestamping history
import json # For serializing history data
from PyQt5.QtGui import QIcon # Import QIcon
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread, QDir, QSettings # Import QThread, QDir, QSettings
from api.peertube_client import PeerTubeClient
from core.queue_manager import UploadQueueManager, TaskStatus # Import UploadQueueManager and TaskStatus
from config import settings # Import the settings module

# Signal for updating GUI from other threads
class GuiSignalEmitter(QObject):
    task_update_signal = pyqtSignal(int, object, int, object, object, bool, object, object, object, bool)
    log_signal = pyqtSignal(str)

    def emit_task_update(self, task_id, status, progress, video_id, error_message, is_new, file_path, title, channel_id, is_removed):
        self.task_update_signal.emit(task_id, status, progress, video_id, error_message, is_new, file_path, title, channel_id, is_removed)

    def emit_log(self, message):
        self.log_signal.emit(message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tadreb.live Video Uploader")

        # Initialize QSettings
        # Using generic names; replace "YourOrg" and "PeerTubeUploader" as appropriate
        self.settings = QSettings("PeerTubeUploaderOrg", "PeerTubeUploaderApp")
        self.log_message(f"QSettings initialized. Backend: {self.settings.format()}, Path: {self.settings.fileName()}")

        # One-time cleanup of old history key, if it exists
        if self.settings.contains("history/uploads"):
            self.settings.remove("history/uploads")
            self.log_message("DEBUG: Removed old format upload history key 'history/uploads'.")

        self.upload_history = []
        self.log_message("DEBUG: Initializing upload_history as []. Calling _load_upload_history...")
        self._load_upload_history() # Loads from "history/uploads_json"
        self.log_message(f"DEBUG: After _load_upload_history, self.upload_history is: {self.upload_history}")

        self.setGeometry(100, 100, 900, 750) # Initial size, user can resize with splitters

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.overall_layout = QVBoxLayout(self.central_widget)
        self.central_widget.setLayout(self.overall_layout)

        app_settings = settings.load_settings()
        self.configured_instance_url = app_settings.get('instance_url', '')
        if not self.configured_instance_url:
            QMessageBox.critical(self, "Configuration Error", "PeerTube instance URL not configured in settings.py!")
            self.log_message("CRITICAL: PeerTube instance URL not configured in settings.py!")

        self.peertube_client = None
        self.queue_manager = None
        self.user_channels = []
        self.full_channel_list = []
        self.selected_channel_id_from_list = None # Initialize new attribute
        self.task_widgets = {}

        self.gui_signals = GuiSignalEmitter()
        self.gui_signals.task_update_signal.connect(self.handle_task_update_signal)
        self.gui_signals.log_signal.connect(self.log_message_from_thread)

        self.configured_username = app_settings.get('username', '')
        self.configured_password = app_settings.get('password', '')

        self.log_section_group = self._create_log_section()
        # self.video_details_group = self._create_top_section() # This now returns None and is not needed here.
        # Its functionalities are merged into local_file_browser_group.
        self.queue_section_group = self._create_queue_section()

        self.main_v_splitter = QSplitter(Qt.Vertical)
        self.main_v_splitter.addWidget(self.log_section_group)

        self.middle_h_splitter = QSplitter(Qt.Horizontal)

        # Create Local File Browser (which now includes the details controls)
        self.local_file_browser_group = self._create_local_file_browser_group()
        # No more video_details_group or left_pane_splitter needed as separate entities.

        self.middle_h_splitter.addWidget(self.local_file_browser_group) # Add directly

        self.channel_browser_group = QGroupBox("Available Channels")
        channel_browser_layout = QVBoxLayout()

        # Create and add the channel search input directly here
        self.channel_search_input = QLineEdit()
        self.channel_search_input.setPlaceholderText("Search channels by name or handle...")
        self.channel_search_input.textChanged.connect(self._on_channel_search_changed)
        search_channel_layout = QHBoxLayout() # Use a QHBoxLayout for label and input
        search_channel_layout.addWidget(QLabel("Search:")) # Keep it concise
        search_channel_layout.addWidget(self.channel_search_input)
        channel_browser_layout.addLayout(search_channel_layout) # Add the layout

        self.channel_list_widget = QListWidget()
        self.channel_list_widget.currentItemChanged.connect(self._on_channel_list_selection_changed)
        channel_browser_layout.addWidget(self.channel_list_widget)
        self.channel_browser_group.setLayout(channel_browser_layout)
        self.middle_h_splitter.addWidget(self.channel_browser_group)

        self.main_v_splitter.addWidget(self.middle_h_splitter)
        self.main_v_splitter.addWidget(self.queue_section_group)

        total_height = self.geometry().height()
        log_height = int(total_height * 0.15)
        middle_area_height = int(total_height * 0.60)
        queue_height = int(total_height * 0.25)
        self.main_v_splitter.setSizes([log_height, middle_area_height, queue_height])

        total_width = self.geometry().width()
        # Adjust splitter for the local_file_browser_group and channel_browser_group
        # self.left_pane_splitter is removed.
        # self.video_details_group is removed / merged.

        # Proportions for middle_h_splitter (local_file_browser_group vs channel_browser_group)
        # Give local browser a bit more space as it now contains more controls.
        local_browser_and_details_width = int(total_width * 0.60)
        channel_browser_width = int(total_width * 0.40)
        self.middle_h_splitter.setSizes([local_browser_and_details_width, channel_browser_width])

        # No more left_pane_splitter to size.

        self.overall_layout.addWidget(self.main_v_splitter)
        self._create_status_bar()

        # Restore splitter states
        v_splitter_state = self.settings.value("gui/main_v_splitter_state")
        if v_splitter_state:
            self.main_v_splitter.restoreState(v_splitter_state)

        h_splitter_state = self.settings.value("gui/middle_h_splitter_state")
        if h_splitter_state:
            self.middle_h_splitter.restoreState(h_splitter_state)

        # The channel_search_input is created in the channel browser setup.
        # Video Details related widgets (file_path_input, title_input, add_to_queue_button)
        # are now created within _create_local_file_browser_group.

        self.log_message(f"Application started. Configured endpoint: {'Provided' if self.configured_instance_url else 'Not Provided'}.")
        # _initialize_local_file_browser is called at the end of _create_local_file_browser_group

        if self.configured_instance_url and self.configured_username:
            self._attempt_auto_connection()
        elif not self.configured_instance_url:
            self.log_message("Auto-connect skipped: Instance URL not configured.")
            self._update_connection_status_indicator(False, "Instance URL not configured")
        else:
            self.log_message("Auto-connect skipped: Username not configured in settings.py.")
            self._update_connection_status_indicator(False, "Username not configured")

        # Call _update_add_to_queue_button_state AFTER all relevant widgets are created.
        # This will be handled by ensuring _create_local_file_browser_group and other UI setup methods
        # correctly initialize widgets before this is first called.
        # self._update_add_to_queue_button_state() # Moved to after widget creation
        self._update_queue_control_button_states()


    def _create_local_file_browser_group(self):
        group = QGroupBox("Local Browser & Upload Details") # Renamed Group
        layout = QVBoxLayout()

        # Directory Tree View
        self.dir_tree_view = QTreeView()
        self.dir_model = QFileSystemModel()
        self.dir_model.setFilter(QDir.NoDotAndDotDot | QDir.AllDirs)
        self.dir_tree_view.setModel(self.dir_model)
        self.dir_tree_view.setHeaderHidden(True)
        for i in range(1, self.dir_model.columnCount()):
            self.dir_tree_view.hideColumn(i)
        self.dir_tree_view.clicked.connect(self._on_local_dir_selected)
        layout.addWidget(self.dir_tree_view) # Add tree view to main layout first

        # Container for Video File, Title, and Add to Queue button
        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 5, 0, 5) # Add some spacing

        # Video File Input
        file_layout = QHBoxLayout()
        self.file_path_input = QLineEdit()
        self.file_path_input.setPlaceholderText("Select video file...")
        self.file_path_input.setReadOnly(True)

        browse_button = QPushButton() # Text removed, icon will be set
        browse_icon = self.style().standardIcon(QStyle.SP_DirOpenIcon)
        browse_button.setIcon(browse_icon)
        browse_button.setToolTip("Browse for video file")
        browse_button.clicked.connect(self.browse_file)

        file_layout.addWidget(QLabel("File:")) # Shorter label
        file_layout.addWidget(self.file_path_input)
        file_layout.addWidget(browse_button)
        controls_layout.addLayout(file_layout)

        # Title Input
        title_layout = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter video title (3-120 characters)")
        self.title_input.textChanged.connect(self._update_add_to_queue_button_state) # Connect here
        title_layout.addWidget(QLabel("Title:"))
        title_layout.addWidget(self.title_input)
        controls_layout.addLayout(title_layout)

        # Add to Queue Button
        self.add_to_queue_button = QPushButton() # Text removed for icon, or can be " Add"
        add_icon = self.style().standardIcon(QStyle.SP_ArrowUp) # Using SP_ArrowUp for "upload"
        self.add_to_queue_button.setIcon(add_icon)
        self.add_to_queue_button.setText("Add to Queue") # Keep text for clarity, icon is a visual aid
        self.add_to_queue_button.setToolTip("Add selected video to the upload queue")
        self.add_to_queue_button.clicked.connect(self.add_to_queue)
        self.add_to_queue_button.setEnabled(False) # Initial state
        controls_layout.addWidget(self.add_to_queue_button, alignment=Qt.AlignCenter)

        layout.addWidget(controls_container) # Add controls container to main layout

        # Files List View
        self.file_list_view = QListView()
        self.file_model = QFileSystemModel()
        self.file_model.setFilter(QDir.NoDotAndDotDot | QDir.Files)
        # Define video file extensions - add more as needed
        video_extensions = ["*.mp4", "*.avi", "*.mkv", "*.mov", "*.webm", "*.flv", "*.wmv"]
        self.file_model.setNameFilters(video_extensions)
        self.file_model.setNameFilterDisables(False) # Ensure filter is active

        self.file_list_view.setModel(self.file_model)
        self.file_list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_list_view.doubleClicked.connect(self._on_local_file_double_clicked)
        layout.addWidget(self.file_list_view) # Add file list view to main layout

        # The splitter is no longer needed here as elements are in QVBoxLayout
        # splitter = QSplitter(Qt.Vertical)
        # splitter.addWidget(self.dir_tree_view) # dir_tree_view is now directly in layout
        # # The bottom part of splitter would be a new widget containing controls + file_list_view
        # bottom_widget = QWidget()
        # bottom_layout = QVBoxLayout(bottom_widget)
        # bottom_layout.addWidget(controls_container)
        # bottom_layout.addWidget(self.file_list_view)
        # bottom_layout.setContentsMargins(0,0,0,0)
        # splitter.addWidget(bottom_widget)
        # splitter.setSizes([150, 450]) # Adjust sizes

        # layout.addWidget(splitter) # Add splitter to group's main layout
        group.setLayout(layout)

        self._initialize_local_file_browser() # Initialize paths for models
        self._update_add_to_queue_button_state() # Crucial: update button state after all related widgets are created

        return group

    def _initialize_local_file_browser(self):
        # Ensure models are created before calling this
        if not hasattr(self, 'dir_model') or not hasattr(self, 'file_model'):
            self.log_message("Error: File browser models not ready for initialization.")
            return

        default_path = QDir.homePath()
        saved_path = self.settings.value("gui/lastLocalPath", default_path)

        if not QDir(saved_path).exists(): # Check if saved path is valid
            self.log_message(f"Saved path '{saved_path}' does not exist. Falling back to home directory.")
            saved_path = default_path
            # Optionally, clear the invalid saved path from settings
            # self.settings.remove("gui/lastLocalPath")

        # Set the dir_model to the filesystem root to allow full navigation
        self.dir_model.setRootPath(QDir.rootPath())
        self.dir_tree_view.setRootIndex(self.dir_model.index(QDir.rootPath())) # Should show drives etc.

        # Now, try to navigate to the saved_path in the tree view
        start_index = self.dir_model.index(saved_path)
        if start_index.isValid():
            self.dir_tree_view.scrollTo(start_index, QAbstractItemView.PositionAtTop)
            self.dir_tree_view.setCurrentIndex(start_index)
            self.dir_tree_view.expand(start_index)

        # The file_model should still be rooted at the specific saved_path initially
        self.file_model.setRootPath(saved_path)
        self.file_list_view.setRootIndex(self.file_model.index(saved_path))
        self.log_message(f"Local file browser tree initialized to filesystem root. Current path set to: {saved_path}")

    def _on_local_dir_selected(self, index):
        path = self.dir_model.filePath(index)
        self.file_list_view.setRootIndex(self.file_model.setRootPath(path))
        self.log_message(f"Local directory selected: {path}")

    def _on_local_file_double_clicked(self, index):
        file_path = self.file_model.filePath(index)
        if os.path.isfile(file_path): # Ensure it's a file
            self.file_path_input.setText(file_path)
            self.log_message(f"Local file selected via browser: {file_path}")

            # Save the directory of the selected file
            directory_path = os.path.dirname(file_path)
            self.settings.setValue("gui/lastLocalPath", directory_path)
            self.log_message(f"Saved last local path: {directory_path}")

            # Automatically set title from filename (reuse logic from browse_file)
            base_name = os.path.basename(file_path)
            title_without_extension, _ = os.path.splitext(base_name)
            self.title_input.setText(title_without_extension)

            self._update_add_to_queue_button_state()
            self.show_status_message(f"File selected: {os.path.basename(file_path)}", 2000)


    def _create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.connection_status_indicator_label = QLabel("● Initializing...")
        self.connection_status_indicator_label.setStyleSheet("padding-left: 5px; padding-right: 5px; color: orange;")
        self.statusBar.addWidget(self.connection_status_indicator_label)


    def _create_top_section(self):
        # This method previously created the "Video Details" group box,
        # including file_path_input, title_input, and add_to_queue_button.
        # These widgets and their functionalities have been moved to _create_local_file_browser_group.
        # This group box ("Video Details") is no longer needed as a separate entity.

        # If there were other elements specific to a "top section" that were not moved,
        # they would remain here. For now, it seems this method's original purpose is fulfilled elsewhere.
        # To avoid errors if it's still called, we can return a dummy QWidget or None,
        # or ensure it's no longer called.
        # For now, let's make it return None, and adjust the call site in __init__.
        return None

    def _create_queue_section(self):
        queue_section_group = QGroupBox("Upload Queue")
        queue_main_layout = QVBoxLayout()

        self.upload_queue_listwidget = QListWidget()
        self.upload_queue_listwidget.setDragEnabled(True)
        self.upload_queue_listwidget.setAcceptDrops(True)
        self.upload_queue_listwidget.setDragDropMode(QAbstractItemView.InternalMove)
        self.upload_queue_listwidget.setDefaultDropAction(Qt.MoveAction)
        if hasattr(self.upload_queue_listwidget.model(), 'rowsMoved'): # Check if signal exists
            self.upload_queue_listwidget.model().rowsMoved.connect(self._on_queue_rows_moved)

        queue_main_layout.addWidget(self.upload_queue_listwidget)

        item_management_buttons_layout = QHBoxLayout()
        self.remove_selected_button = QPushButton("Remove Selected Task")
        self.remove_selected_button.clicked.connect(self.remove_selected_task_from_queue)
        self.clear_completed_button = QPushButton("Clear Completed Tasks")
        self.clear_completed_button.clicked.connect(self.clear_completed_tasks_in_queue)
        item_management_buttons_layout.addWidget(self.remove_selected_button)
        item_management_buttons_layout.addWidget(self.clear_completed_button)
        queue_main_layout.addLayout(item_management_buttons_layout)

        queue_control_buttons_layout = QHBoxLayout()
        self.start_pause_button = QPushButton("Pause Queue")
        self.start_pause_button.clicked.connect(self._on_start_pause_queue_clicked)
        self.start_pause_button.setEnabled(False)

        self.stop_all_clear_button = QPushButton("Stop All & Clear Queue")
        self.stop_all_clear_button.clicked.connect(self._on_stop_all_clear_queue_clicked)
        self.stop_all_clear_button.setEnabled(False)

        queue_control_buttons_layout.addWidget(self.start_pause_button)
        queue_control_buttons_layout.addWidget(self.stop_all_clear_button)
        queue_main_layout.addLayout(queue_control_buttons_layout)

        queue_section_group.setLayout(queue_main_layout)
        return queue_section_group

    def _create_log_section(self):
        log_section_group = QGroupBox("Logs")
        log_layout = QVBoxLayout()
        self.log_output_area = QTextEdit()
        self.log_output_area.setReadOnly(True)
        log_layout.addWidget(self.log_output_area)
        log_section_group.setLayout(log_layout)
        return log_section_group

    def _update_connection_status_indicator(self, connected, event_message=""):
        text_color = "black"
        circle_char = "●"
        status_description = ""
        service_name = "tadreb.live"

        if connected is True:
            indicator_color_name = "green"
            status_description = f"Connected to {service_name}"
            if event_message:
                status_description = f"{event_message}"
        elif connected is False:
            indicator_color_name = "red"
            status_description = f"Disconnected from {service_name}"
            if event_message:
                status_description = f"{service_name}: {event_message}"
        elif connected is None:
            indicator_color_name = "orange"
            status_description = f"Connecting to {service_name}..."
            if event_message:
                 status_description = f"{event_message}"
        else:
            indicator_color_name = "grey"
            status_description = "Status Unknown"

        full_text = f"{circle_char} {status_description}"

        if hasattr(self, 'connection_status_indicator_label'):
            self.connection_status_indicator_label.setText(full_text)
            self.connection_status_indicator_label.setStyleSheet(
                f"color: {indicator_color_name}; padding-left: 5px; padding-right: 5px;"
            )
            self.log_message(f"[StatusIndicator] Updated: {full_text} (Color: {indicator_color_name})")
        else:
            self.log_message("Error: connection_status_indicator_label not found during update.")

    def log_message_from_thread(self, message):
        self.log_output_area.append(message)
        print(message)

    def log_message(self, message):
        if hasattr(self, 'log_output_area') and self.log_output_area is not None: # Check if log_output_area exists
            if QThread.currentThread() == QApplication.instance().thread():
                self.log_output_area.append(message)
                print(message)
            else:
                self.gui_signals.emit_log(message)
        else: # Fallback if log area not ready (e.g. very early messages)
            print(f"[Early Log] {message}")


    def show_status_message(self, message, timeout=3000):
        if hasattr(self, 'statusBar'): # Check if statusBar exists
            self.statusBar.showMessage(message, timeout)

    def _attempt_auto_connection(self):
        if not self.configured_instance_url or not self.configured_username or self.configured_password is None:
            self.log_message("Auto-connect failed: Missing instance URL, username, or password in configuration.")
            self._update_connection_status_indicator(False, "Configuration incomplete")
            self.show_status_message("Auto-connect failed: Configuration incomplete.", 5000)
            return

        self.log_message("Attempting automatic connection to the configured tadreb.live service...")
        self._update_connection_status_indicator(None, "Connecting...")
        self.show_status_message(f"Attempting auto-connection to {self.configured_instance_url}...")

        self._perform_connection_logic(self.configured_username, self.configured_password)

    def _perform_connection_logic(self, username, password):
        if self.queue_manager and self.queue_manager.is_processing:
            self.queue_manager.stop_processing()
            self.log_message("Stopped ongoing queue processing for (re)connection attempt.")

        self.peertube_client = PeerTubeClient(self.configured_instance_url)

        self.log_message(f"Authenticating user {username}...")
        self._update_connection_status_indicator(None, "Authenticating...")
        self.show_status_message(f"Authenticating {username}...")

        if self.peertube_client.authenticate(username, password):
            self.log_message("Authentication successful!")
            self.show_status_message("Authentication successful!", 5000)
            self._update_connection_status_indicator(True)

            if self.queue_manager:
                self.queue_manager.peertube_client = self.peertube_client
                self.queue_manager.start_processing()
                self.log_message("Updated PeerTube client for existing QueueManager and restarted queue.")
            else:
                self.queue_manager = UploadQueueManager(
                    peertube_client=self.peertube_client,
                    status_update_callback=self.gui_signals.emit_task_update,
                    log_callback=self.gui_signals.emit_log,
                    upload_chunk_size_mb=4
                )
                self.log_message("UploadQueueManager initialized with 4MB chunk size.")
            self.load_channels()
        else:
            self.log_message("Authentication failed. Check credentials in settings.py or server status.")
            self.show_status_message("Authentication failed.", 5000)
            self._update_connection_status_indicator(False, "Connection Failed. Check logs.")
            self.peertube_client = None
            # self.channel_combo.clear() # Old combo
            # self.channel_combo.addItem("Connection Failed")
            # self.channel_combo.setEnabled(False)
            if hasattr(self, 'channel_list_widget'): # New list widget
                self._populate_channel_list_widget(None) # Show failed state in list
            self.add_to_queue_button.setEnabled(False)


    def load_channels(self):
        if not self.peertube_client or not self.peertube_client.access_token:
            self.log_message("Cannot load channels: Not authenticated or client not initialized.")
            QMessageBox.warning(self, "Error", "Not authenticated. Please connect and authenticate first.")
            self.add_to_queue_button.setEnabled(False)
            self._populate_channel_list_widget(None)
            return

        self.log_message("Loading channels...")
        self.show_status_message("Loading channels...")
        self.add_to_queue_button.setEnabled(False)

        api_channels_data = self.peertube_client.get_channels()
        self.full_channel_list = []
        self.user_channels = []

        if api_channels_data is not None:
            self.full_channel_list = api_channels_data

            if self.full_channel_list:
                self.full_channel_list.sort(key=lambda ch: ch['displayName'].lower())
                self.user_channels = list(self.full_channel_list)
                self.log_message(f"Loaded and sorted {len(self.full_channel_list)} channels.")
                self.show_status_message(f"Loaded {len(self.full_channel_list)} channels.", 3000)
                self._populate_channel_list_widget(self.full_channel_list)
            else:
                self.log_message("No channels found for your account or instance.")
                self._populate_channel_list_widget([])
                self.show_status_message("No channels found.", 3000)
        else:
            self.log_message("Failed to load channels. See logs.")
            self._populate_channel_list_widget(None)
            QMessageBox.critical(self, "Error", "Failed to load channels from the PeerTube instance.")
            self.show_status_message("Failed to load channels.", 3000)

        if hasattr(self, 'channel_list_widget') and self.channel_list_widget.count() == 0 :
             self.add_to_queue_button.setEnabled(False)

    def _populate_channel_list_widget(self, channels_to_display):
        if not hasattr(self, 'channel_list_widget'):
            return

        self.channel_list_widget.clear()
        self.add_to_queue_button.setEnabled(False)

        if channels_to_display is None:
            item = QListWidgetItem("Failed to load channels")
            self.channel_list_widget.addItem(item)
            self.channel_list_widget.setEnabled(False)
        elif not channels_to_display:
            item = QListWidgetItem("No channels found")
            self.channel_list_widget.addItem(item)
            self.channel_list_widget.setEnabled(True)
        else:
            self.channel_list_widget.setEnabled(True)
            for channel in channels_to_display:
                display_text = f"{channel['displayName']} (Handle: {channel['name']})"
                owner_display = channel.get('ownerAccountName', 'N/A')
                is_own_channel = False
                if self.peertube_client and self.peertube_client.username:
                    if owner_display == self.peertube_client.username or \
                       owner_display.startswith(self.peertube_client.username + "@"):
                        is_own_channel = True

                if (self.peertube_client and self.peertube_client.user_role_id in [0, 1]) and \
                   not is_own_channel and owner_display != 'N/A':
                    display_text += f" (Owner: {owner_display})"

                list_item = QListWidgetItem(display_text)
                list_item.setData(Qt.UserRole, channel['id'])
                self.channel_list_widget.addItem(list_item)

    def _check_title_validity(self):
        """Checks if the current title input is valid according to defined rules."""
        if not hasattr(self, 'title_input'): return False
        title = self.title_input.text().strip()
        return 3 <= len(title) <= 120

    def _update_add_to_queue_button_state(self):
        """Updates the enabled state of the 'Add to Queue' button."""
        if not hasattr(self, 'file_path_input'): return

        file_path_ok = bool(self.file_path_input.text())
        title_ok = self._check_title_validity()
        channel_ok = hasattr(self, 'selected_channel_id_from_list') and self.selected_channel_id_from_list is not None

        connected_ok = self.peertube_client and self.peertube_client.access_token is not None

        if file_path_ok and title_ok and channel_ok and connected_ok:
            self.add_to_queue_button.setEnabled(True)
        else:
            self.add_to_queue_button.setEnabled(False)

    def _on_channel_list_selection_changed(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        """
        Handles selection changes in the channel_list_widget.
        Updates the 'Add to Queue' button state.
        """
        self.selected_channel_id_from_list = None
        if current_item is not None:
            channel_id = current_item.data(Qt.UserRole)
            if channel_id is not None:
                self.selected_channel_id_from_list = channel_id
                self.log_message(f"Channel selected from list: ID {channel_id} - {current_item.text()}")
            else:
                self.log_message(f"Informational item selected in channel list: {current_item.text()}")
        else:
            self.log_message("Channel list selection cleared.")

        self._update_add_to_queue_button_state()

    def _on_channel_search_changed(self, search_text):
        """
        Filters the channel list in the QListWidget based on the search_text.
        """
        if not hasattr(self, 'full_channel_list'):
            self._populate_channel_list_widget(None)
            return

        search_text_lower = search_text.lower().strip()

        if not search_text_lower:
            self._populate_channel_list_widget(self.full_channel_list)
            return

        if self.full_channel_list is None:
             self._populate_channel_list_widget(None)
             return

        filtered_channels = [
            ch for ch in self.full_channel_list
            if search_text_lower in ch['displayName'].lower() or \
               search_text_lower in ch['name'].lower()
        ]

        self._populate_channel_list_widget(filtered_channels)

    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select Video File", "", "Video Files (*.mp4 *.avi *.mkv *.mov *.webm);;All Files (*)")
        if file_name:
            self.file_path_input.setText(file_name)
            self.log_message(f"Selected file: {file_name}")
            self.show_status_message(f"File selected: {os.path.basename(file_name)}", 2000)

            # Save the directory of the selected file
            directory_path = os.path.dirname(file_name)
            self.settings.setValue("gui/lastLocalPath", directory_path)
            self.log_message(f"Saved last local path from browse: {directory_path}")

            # Automatically set title from filename
            base_name = os.path.basename(file_name)
            title_without_extension, _ = os.path.splitext(base_name)
            self.title_input.setText(title_without_extension)
            # self.title_input.textChanged.emit(self.title_input.text()) # Ensure dependent actions trigger if any

            self._update_add_to_queue_button_state() # This will also check title validity

    def add_to_queue(self):
        if not self.queue_manager:
            QMessageBox.warning(self, "Not Connected", "Please connect and authenticate to a PeerTube instance first.")
            self.log_message("Error: Cannot add to queue, QueueManager not initialized (not connected).")
            return

        file_path = self.file_path_input.text()
        title = self.title_input.text().strip()

        channel_id = self.selected_channel_id_from_list

        if not file_path:
            QMessageBox.warning(self, "Input Error", "Please select a video file.")
            return

        if channel_id is None:
            QMessageBox.warning(self, "Input Error", "Please select a channel from the list.")
            return

        if not title:
            QMessageBox.warning(self, "Input Error", "Please enter a video title.")
            return
        if not self._check_title_validity(): # Use the helper here for consistency
            QMessageBox.warning(self, "Input Error", "Video title must be between 3 and 120 characters.")
            self.log_message("Error: Video title must be between 3 and 120 characters.")
            return

        task_id = self.queue_manager.add_task(
            file_path=file_path,
            channel_id=channel_id,
            title=title
        )
        self.log_message(f"Task '{title}' (ID: {task_id}) added to internal queue.")
        self.show_status_message(f"Added '{title}' to queue.", 2000)

        self.file_path_input.clear()
        self.title_input.clear()
        self.channel_list_widget.setCurrentItem(None) # Clear selection in list widget
        self._update_add_to_queue_button_state()
        self._update_queue_control_button_states()

    def handle_task_update_signal(self, task_id, status_enum, progress, video_id, error_message, is_new, file_path, title, channel_id_from_cb, is_removed):
        if is_removed:
            if task_id in self.task_widgets:
                item_to_remove = self.task_widgets.pop(task_id)
                self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(item_to_remove))
                self.log_message(f"Removed task {task_id} from GUI.")
            self._update_queue_control_button_states() # Update buttons as queue content changed
            return

        item = self.task_widgets.get(task_id)

        if is_new and not item:
            channel_display_name = "Unknown Channel"
            for ch_data in self.user_channels:
                if ch_data['id'] == channel_id_from_cb:
                    channel_display_name = ch_data['displayName']
                    if self.peertube_client and self.peertube_client.user_role_id in [0,1] and \
                       ch_data.get('ownerAccountName') != self.peertube_client.username and \
                       not ch_data.get('ownerAccountName', 'N/A').startswith(self.peertube_client.username + "@") and \
                       ch_data.get('ownerAccountName') != 'N/A':
                        channel_display_name += f" (Owner: {ch_data['ownerAccountName']})"
                    break

            base_text = f"ID: {task_id} - Title: {title} - Channel: {channel_display_name}"
            item = QListWidgetItem()
            self.task_widgets[task_id] = item
            self.upload_queue_listwidget.addItem(item)

            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 2, 5, 2)

            self.task_widgets[task_id] = {
                'item': item,
                'label': QLabel(base_text),
                'progress_bar': QProgressBar(),
                'status_label': QLabel(f" {status_enum.value}"),
                'copy_url_button': QPushButton() # Create button, configure later
            }

            copy_btn = self.task_widgets[task_id]['copy_url_button']
            copy_icon = self.style().standardIcon(QStyle.SP_FileDialogContentsView) # Generic icon, find better if possible
            copy_btn.setIcon(copy_icon)
            copy_btn.setToolTip("Copy Video URL to Clipboard")
            copy_btn.setFixedSize(24, 24) # Small icon button
            copy_btn.setVisible(False) # Hidden by default
            copy_btn.clicked.connect(self._copy_video_url_to_clipboard)


            self.task_widgets[task_id]['label'].setWordWrap(True)
            item_layout.addWidget(self.task_widgets[task_id]['label'], 1) # Label takes most space

            self.task_widgets[task_id]['progress_bar'].setRange(0, 100)
            self.task_widgets[task_id]['progress_bar'].setValue(0)
            self.task_widgets[task_id]['progress_bar'].setTextVisible(True)
            self.task_widgets[task_id]['progress_bar'].setFixedSize(120, 18) # Progress bar size
            item_layout.addWidget(self.task_widgets[task_id]['progress_bar'])

            self.task_widgets[task_id]['status_label'].setFixedWidth(80) # Status label size
            item_layout.addWidget(self.task_widgets[task_id]['status_label'])

            item_layout.addWidget(copy_btn) # Add copy button to layout

            item_widget.setLayout(item_layout)
            item.setSizeHint(item_widget.sizeHint())
            self.upload_queue_listwidget.setItemWidget(item, item_widget)
            self.log_message(f"Added task {task_id} to GUI queue: {title}")

        if task_id in self.task_widgets and isinstance(self.task_widgets[task_id], dict) and 'label' in self.task_widgets[task_id]:
            task_gui_parts = self.task_widgets[task_id]
            task_gui_parts['status_label'].setText(f" {status_enum.value}")
            task_gui_parts['progress_bar'].setValue(progress if progress is not None else 0)
            copy_button = task_gui_parts['copy_url_button'] # Get the button

            current_label_text = task_gui_parts['label'].text()
            video_id_text_segment = f" (Video ID: {video_id})"

            is_terminal_state = False
            video_url = None

            if status_enum == TaskStatus.FAILED:
                task_gui_parts['label'].setStyleSheet("color: red;")
                task_gui_parts['status_label'].setStyleSheet("color: red;")
                copy_button.setVisible(False)
                self.log_message(f"Task {task_id} ('{title}') FAILED: {error_message}")
                is_terminal_state = True
            elif status_enum == TaskStatus.COMPLETED:
                task_gui_parts['label'].setStyleSheet("color: green;")
                task_gui_parts['status_label'].setStyleSheet("color: green;")
                if video_id:
                    if video_id_text_segment not in current_label_text:
                        task_gui_parts['label'].setText(current_label_text + video_id_text_segment)
                    video_url = self._get_video_url(video_id)
                    if video_url:
                        copy_button.setProperty("video_url", video_url)
                        copy_button.setVisible(True)
                    else:
                        copy_button.setVisible(False)
                else:
                    copy_button.setVisible(False)
                is_terminal_state = True
            elif status_enum == TaskStatus.CANCELLED:
                task_gui_parts['label'].setStyleSheet("color: orange;")
                task_gui_parts['status_label'].setStyleSheet("color: orange;")
                copy_button.setVisible(False)
                is_terminal_state = True
            else: # Not a terminal state
                task_gui_parts['label'].setStyleSheet("")
                task_gui_parts['status_label'].setStyleSheet("")
                copy_button.setVisible(False) # Hide for non-terminal states
                if video_id_text_segment in current_label_text:
                     task_gui_parts['label'].setText(current_label_text.replace(video_id_text_segment, ""))

            if is_terminal_state:
                # Attempt to find channel name for history
                # video_url is already set if COMPLETED and video_id exists
                channel_name_for_history = "Unknown Channel"
                if hasattr(self, 'user_channels'): # Ensure user_channels is available
                    for ch_data in self.user_channels:
                        if ch_data['id'] == channel_id_from_cb: # channel_id_from_cb is task.channel_id
                            channel_name_for_history = ch_data['displayName']
                            break

                # Placeholder for video_url, will be properly filled in next step
                # For now, it's passed to _add_to_upload_history and might be None for FAILED/CANCELLED
                # or if video_id is not yet available for COMPLETED (though it should be).
                # The _get_video_url method will be used in the next step.
                current_ts = datetime.datetime.now().isoformat()

                # video_url_for_history will be properly generated in the next step
                # For now, we'll call _get_video_url if status is COMPLETED and video_id exists.
                # This anticipates the next step.
                # video_url is already derived above if task is COMPLETED.
                # if status_enum == TaskStatus.COMPLETED and video_id:
                #     video_url = self._get_video_url(video_id) # video_url is already set

                self._add_to_upload_history(
                    title=title,
                    file_path=file_path,
                    video_id=video_id,
                    video_url=video_url, # Use the derived video_url
                    timestamp=current_ts,
                    status=status_enum,
                    channel_id=channel_id_from_cb,
                    channel_name_hint=channel_name_for_history
                )

        self._update_queue_control_button_states()


    def remove_selected_task_from_queue(self):
        selected_list_items = self.upload_queue_listwidget.selectedItems()
        if not selected_list_items:
            QMessageBox.information(self, "No Selection", "Please select a task from the queue to remove.")
            return

        list_item_to_remove = selected_list_items[0]

        task_id_to_remove = None
        for tid, widget_data in self.task_widgets.items():
            if isinstance(widget_data, dict) and widget_data.get('item') == list_item_to_remove:
                task_id_to_remove = tid
                break

        if task_id_to_remove and self.queue_manager:
            confirm = QMessageBox.question(self, "Confirm Remove",
                                           f"Are you sure you want to remove task ID {task_id_to_remove}?",
                                           QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                if self.queue_manager.remove_task(task_id_to_remove):
                    self.log_message(f"Request to remove task {task_id_to_remove} sent to queue manager.")
                else:
                    self.log_message(f"Task {task_id_to_remove} not in queue manager, removing from GUI only.")
                    if task_id_to_remove in self.task_widgets:
                        popped_item_data = self.task_widgets.pop(task_id_to_remove)
                        if isinstance(popped_item_data, dict):
                             self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(popped_item_data['item']))
                        self.log_message(f"Removed task {task_id_to_remove} from GUI.")
        elif not self.queue_manager:
             self.log_message("Queue manager not available to remove task.")
        # _update_queue_control_button_states() will be called via handle_task_update_signal if manager confirms removal


    def clear_completed_tasks_in_queue(self):
        if not self.queue_manager:
            self.log_message("Queue manager not available to clear tasks.")
            return

        confirm = QMessageBox.question(self, "Confirm Clear",
                                       "Are you sure you want to remove all COMPLETED tasks from the list?",
                                       QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            tasks_to_remove_ids = []
            for task_id, widget_data in list(self.task_widgets.items()):
                if isinstance(widget_data, dict) and widget_data['status_label'].text().strip() == TaskStatus.COMPLETED.value:
                    tasks_to_remove_ids.append(task_id)

            if not tasks_to_remove_ids:
                self.log_message("No completed tasks found in the GUI list to clear.")
                self._update_queue_control_button_states() # Update in case list was empty but button was somehow enabled
                return

            for task_id in tasks_to_remove_ids:
                self.queue_manager.remove_task(task_id)
            self.log_message(f"Requested removal of {len(tasks_to_remove_ids)} completed tasks.")
        # _update_queue_control_button_states() called by handle_task_update_signal after removal


    def closeEvent(self, event):
        self.log_message("Main window closing...")

        # Save splitter states
        self.settings.setValue("gui/main_v_splitter_state", self.main_v_splitter.saveState())
        self.settings.setValue("gui/middle_h_splitter_state", self.middle_h_splitter.saveState())

        self.settings.setValue("gui/middle_h_splitter_state", self.middle_h_splitter.saveState())
        self.settings.setValue("gui/middle_h_splitter_state", self.middle_h_splitter.saveState())
        self.log_message("DEBUG: Splitter states prepared for saving.")

        # self.log_message("DEBUG: Calling _save_upload_history from closeEvent...")
        # self._save_upload_history() # Save history on close
        # Relying on saves from _add_to_upload_history. If history could be altered elsewhere without saving,
        # this might need to be re-instated or that alteration point needs to save.
        self.log_message("DEBUG: History is saved when items are added.")

        if self.queue_manager:
            self.log_message("Stopping queue manager...")
            self.queue_manager.stop_processing() # This might take a moment

        self.log_message("DEBUG: Before super().closeEvent() in closeEvent.")
        super().closeEvent(event)
        self.log_message("DEBUG: After super().closeEvent() in closeEvent.")

        # Explicit final sync for QSettings AFTER Qt's close processing.
        if hasattr(self, 'settings') and self.settings is not None:
            self.log_message("DEBUG: Performing final QSettings.sync() at the end of closeEvent.")
            self.settings.sync()
            status = self.settings.status()
            self.log_message(f"DEBUG: Final QSettings sync status: {status}")
            if status != QSettings.NoError:
                self.log_message(f"ERROR: Final QSettings sync in closeEvent reported an error: {status}")

            # Attempt to ensure QSettings destructor runs and flushes
            self.log_message("DEBUG: Deleting self.settings in closeEvent.")
            del self.settings
            self.settings = None # Ensure it's not accidentally accessed later if app doesn't fully exit

        self.log_message("Application closed.")

    def _load_upload_history(self):
        self.log_message("DEBUG: Attempting to load upload history...")
        json_string = self.settings.value("history/uploads_json", None)
        self.log_message(f"DEBUG: Raw JSON string from settings: '{json_string}' (type: {type(json_string)})")

        if json_string and isinstance(json_string, str) and len(json_string.strip()) > 0 :
            try:
                loaded_history = json.loads(json_string)
                self.log_message(f"DEBUG: Successfully parsed JSON. Loaded items: {len(loaded_history)}")
                # Optional: Convert status strings back to TaskStatus enums if needed for internal logic.
                # For now, history entries will store status as strings as saved by the modified _save_upload_history.
                self.upload_history = loaded_history
            except json.JSONDecodeError as e:
                self.log_message(f"DEBUG: Error decoding upload history from JSON: {e}. Raw string was: '{json_string}'. Initializing empty history.")
                self.upload_history = []
            except Exception as e:
                self.log_message(f"DEBUG: Unexpected error loading/parsing upload history: {e}. Initializing empty history.")
                self.upload_history = []
        else:
            self.log_message("DEBUG: No valid JSON upload history string found in settings. Initializing empty history.")
            self.upload_history = []

        # Ensure self.upload_history is always a list
        if not isinstance(self.upload_history, list):
            self.log_message(f"DEBUG: Upload history was not a list after loading (type: {type(self.upload_history)}). Resetting to empty list.")
            self.upload_history = []
        self.log_message(f"DEBUG: _load_upload_history complete. Final history length: {len(self.upload_history)}")


    def _save_upload_history(self):
        self.log_message(f"DEBUG: Attempting to save upload history. Current history length: {len(self.upload_history)}")
        self.log_message(f"DEBUG: History content before dump: {self.upload_history}")
        try:
            # _add_to_upload_history now ensures 'status' is stored as a string value.
            # So, self.upload_history can be directly serialized.
            json_string = json.dumps(self.upload_history, indent=2) # Added indent for readability if inspecting file
            self.log_message(f"DEBUG: Serialized JSON string for history: {json_string}")
            self.settings.setValue("history/uploads_json", json_string)
            self.settings.sync() # Force write
            status = self.settings.status()
            self.log_message(f"DEBUG: QSettings.setValue for history done. QSettings status: {status}")
            if status != QSettings.NoError:
                self.log_message(f"ERROR: QSettings reported an error during history save: {status}")

        except TypeError as e:
            self.log_message(f"DEBUG: Error serializing upload history to JSON: {e}")
        except Exception as e:
            self.log_message(f"DEBUG: Unexpected error saving upload history: {e}")

    def _get_video_url(self, video_id):
        if not self.configured_instance_url or not video_id:
            return None
        # Common PeerTube URL structure for short links. Adjust if instance uses a different pattern.
        return f"{self.configured_instance_url.rstrip('/')}/w/{video_id}"

    def _copy_video_url_to_clipboard(self):
        sender_button = self.sender()
        if sender_button and isinstance(sender_button, QPushButton):
            video_url = sender_button.property("video_url")
            if video_url:
                QApplication.clipboard().setText(video_url)
                self.show_status_message("Video URL copied to clipboard!", 3000)
                self.log_message(f"Copied URL to clipboard: {video_url}")
            else:
                self.log_message("Copy URL button clicked, but no URL found in property.")
        else:
            self.log_message("Copy URL action triggered by unexpected sender.")


    def _add_to_upload_history(self, title, file_path, video_id, video_url, timestamp, status, channel_id, channel_name_hint):
        entry_status_value = status.value if hasattr(status, 'value') else str(status)
        self.log_message(f"DEBUG: Adding to upload history: Title='{title}', Status='{entry_status_value}'")
        # Create history entry
        entry = {
            "title": title,
            "file_path": file_path,
            "video_id": video_id,
            "video_url": video_url,
            "timestamp": timestamp, # Should be ISO format string or similar
            "status": entry_status_value, # Store enum value as string
            "channel_id": channel_id,
            "channel_name_hint": channel_name_hint # Store a hint, actual name might change
        }

        self.upload_history.insert(0, entry) # Add to the beginning (most recent first)
        self.log_message(f"DEBUG: History after insert (before trim): {self.upload_history}")

        # Keep history limited to 30 items
        max_history_items = 30
        if len(self.upload_history) > max_history_items:
            self.upload_history = self.upload_history[:max_history_items]
            self.log_message(f"DEBUG: History trimmed to {max_history_items} items.")

        self.log_message(f"DEBUG: History before calling _save_upload_history from _add_to_upload_history: {self.upload_history}")
        self._save_upload_history() # Persist after each addition
        self.log_message(f"DEBUG: Added to upload history: '{title}'. New history size: {len(self.upload_history)}.")


    def _update_queue_control_button_states(self):
        """Updates the enabled state and text of queue control buttons."""
        queue_is_empty_or_manager_missing = not self.queue_manager or not self.queue_manager.queue

        # Stop All & Clear button
        self.stop_all_clear_button.setEnabled(not queue_is_empty_or_manager_missing)

        # Remove Selected Task button
        # Enabled if queue is not empty AND an item is selected in the task list widget
        item_selected_in_task_list = bool(self.upload_queue_listwidget.selectedItems())
        self.remove_selected_button.setEnabled(not queue_is_empty_or_manager_missing and item_selected_in_task_list)

        # Clear Completed Tasks button
        # Enabled if queue is not empty AND there is at least one completed task
        has_completed = False
        if not queue_is_empty_or_manager_missing:
            has_completed = any(task.status == TaskStatus.COMPLETED for task in self.queue_manager.queue)
        self.clear_completed_button.setEnabled(has_completed)

        # Start/Pause/Resume button
        if queue_is_empty_or_manager_missing:
            self.start_pause_button.setEnabled(False)
            self.start_pause_button.setText("Start Queue") # Default for empty/idle
        elif self.queue_manager.is_processing:
            self.start_pause_button.setEnabled(True)
            if self.queue_manager.is_manually_paused:
                self.start_pause_button.setText("Resume Queue")
            else:
                self.start_pause_button.setText("Pause Queue")
        else: # Not currently processing (e.g., all tasks done, or stopped by error/user)
            has_pending = any(task.status == TaskStatus.PENDING for task in self.queue_manager.queue)
            if has_pending:
                self.start_pause_button.setText("Start Queue")
                self.start_pause_button.setEnabled(True)
            else: # No pending tasks, queue is effectively idle or all done/failed/cancelled
                self.start_pause_button.setText("Start Queue")
                self.start_pause_button.setEnabled(False) # No pending tasks to start


    def _on_start_pause_queue_clicked(self):
        if not self.queue_manager:
            self.log_message("Queue manager not available.")
            return

        if self.queue_manager.is_processing and not self.queue_manager.is_manually_paused:
            self.queue_manager.pause_processing()
            self.log_message("User paused queue processing.")
        elif self.queue_manager.is_manually_paused:
            self.queue_manager.resume_processing()
            self.log_message("User resumed queue processing.")
        elif not self.queue_manager.is_processing:
            has_pending = False
            if self.queue_manager.queue: # Check if queue attribute exists and is not empty
                has_pending = any(task.status == TaskStatus.PENDING for task in self.queue_manager.queue)

            if has_pending:
                self.log_message("User started queue processing.")
                self.queue_manager.start_processing()
            else:
                self.log_message("Start Queue clicked, but no pending tasks.")

        self._update_queue_control_button_states()


    def _on_stop_all_clear_queue_clicked(self):
        if not self.queue_manager:
            self.log_message("Queue manager not available for stop all.")
            self._update_queue_control_button_states() # Ensure buttons are in correct state
            return
        if not self.queue_manager.queue:
            QMessageBox.information(self, "Queue Empty", "The upload queue is already empty.")
            self._update_queue_control_button_states()
            return

        confirm = QMessageBox.question(self, "Confirm Stop All & Clear",
                                       "Are you sure you want to stop all ongoing uploads and clear the entire queue?\n"
                                       "This action cannot be undone.",
                                       QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.log_message("User confirmed Stop All & Clear Queue.")
            self.queue_manager.stop_all_and_clear_tasks()

        self._update_queue_control_button_states()

    def _on_queue_rows_moved(self, parent_index, start_row, end_row, destination_index, dest_row):
        """
        Called when rows (tasks) are moved in the upload_queue_listwidget
        due to drag and drop.
        Updates the internal queue order in UploadQueueManager.
        """
        self.log_message(f"Queue rows moved: from {start_row}-{end_row} to {dest_row} under destination {destination_index.row()}")

        if not self.queue_manager:
            self.log_message("Error: Queue manager not available for reordering.")
            return

        new_task_id_order = []
        for i in range(self.upload_queue_listwidget.count()):
            list_item = self.upload_queue_listwidget.item(i)
            # Find the task_id associated with this list_item.
            # self.task_widgets stores {task_id: {'item': QListWidgetItem, ...}}
            task_id_found = None
            for tid, widget_data in self.task_widgets.items():
                if isinstance(widget_data, dict) and widget_data.get('item') == list_item:
                    task_id_found = tid
                    break
            if task_id_found is not None:
                new_task_id_order.append(task_id_found)
            else:
                self.log_message(f"Warning: Could not find task_id for list item at visual row {i} during reorder.")

        if new_task_id_order:
            self.log_message(f"New visual task ID order: {new_task_id_order}")
            self.queue_manager.reorder_queue(new_task_id_order)
        else:
            self.log_message("Warning: No task IDs collected after reorder, queue not updated in manager.")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())
