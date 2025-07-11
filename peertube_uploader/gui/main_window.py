import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog,
                             QComboBox, QGroupBox, QTextEdit, QListWidget, QListWidgetItem,
                             QInputDialog, QMessageBox, QStatusBar, QProgressBar)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread # Import QThread
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
        self.setGeometry(100, 100, 900, 750)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        app_settings = settings.load_settings()
        self.configured_instance_url = app_settings.get('instance_url', '')
        if not self.configured_instance_url:
            QMessageBox.critical(self, "Configuration Error", "PeerTube instance URL not configured in settings.py!")
            self.log_message("CRITICAL: PeerTube instance URL not configured in settings.py!")
            # Consider exiting or disabling connect button: self.connect_button.setEnabled(False)

        self.peertube_client = None
        self.queue_manager = None
        self.user_channels = [] # Will store the list of channels for the combo box (filtered and sorted)
        self.full_channel_list = [] # Will store the complete list of channels from API before filtering/sorting
        self.task_widgets = {}

        self.gui_signals = GuiSignalEmitter()
        self.gui_signals.task_update_signal.connect(self.handle_task_update_signal)
        self.gui_signals.log_signal.connect(self.log_message_from_thread)

        self.configured_username = app_settings.get('username', '')
        self.configured_password = app_settings.get('password', '')

        self._create_top_section()
        self._create_queue_section()
        self._create_log_section()
        self._create_status_bar() # Ensure status bar is created before attempting to update it

        self.log_message(f"Application started. Configured endpoint: {'Provided' if self.configured_instance_url else 'Not Provided'}.")

        if self.configured_instance_url and self.configured_username: # Only attempt auto-connect if URL and user are set
            self._attempt_auto_connection()
        elif not self.configured_instance_url:
            self.log_message("Auto-connect skipped: Instance URL not configured.")
            self._update_connection_status_indicator(False, "Instance URL not configured") # Simpler message
        else: # URL is there but username/password might be missing from config
            self.log_message("Auto-connect skipped: Username not configured in settings.py.")
            self._update_connection_status_indicator(False, "Username not configured") # Simpler message


    def _create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

        # Create the new connection status indicator label for the status bar
        # It will be added using addWidget for left alignment.
        self.connection_status_indicator_label = QLabel("● Initializing...") # Default text with circle
        self.connection_status_indicator_label.setStyleSheet("padding-left: 5px; padding-right: 5px; color: orange;") # Default to orange
        self.statusBar.addWidget(self.connection_status_indicator_label) # Add to the left side


    def _create_top_section(self):
        top_section_group = QGroupBox("Video Details") # Renamed as connection is now auto/implicit
        top_layout = QVBoxLayout()

        # Connection Status Label (replaces connect button) - REMOVED
        # self.connection_status_label = QLabel("Status: Initializing...")
        # self.connection_status_label.setAlignment(Qt.AlignCenter)
        # top_layout.addWidget(self.connection_status_label)

        # File Selection
        file_layout = QHBoxLayout()
        self.file_path_input = QLineEdit()
        self.file_path_input.setPlaceholderText("Select video file...")
        self.file_path_input.setReadOnly(True)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_file)
        file_layout.addWidget(QLabel("Video File:"))
        file_layout.addWidget(self.file_path_input)
        file_layout.addWidget(browse_button)
        top_layout.addLayout(file_layout)

        # Channel Search Input
        search_layout = QHBoxLayout()
        self.channel_search_input = QLineEdit()
        self.channel_search_input.setPlaceholderText("Search channels by name or handle...")
        self.channel_search_input.textChanged.connect(self._on_channel_search_changed) # Connect the signal
        search_layout.addWidget(QLabel("Search Channel:"))
        search_layout.addWidget(self.channel_search_input)
        top_layout.addLayout(search_layout)

        # Channel Selection
        channel_layout = QHBoxLayout()
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Connect to instance first")
        self.channel_combo.setEnabled(False)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_selection_change)
        channel_layout.addWidget(QLabel("Channel:"))
        channel_layout.addWidget(self.channel_combo, 1)
        top_layout.addLayout(channel_layout)

        # Title
        title_layout = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter video title")
        title_layout.addWidget(QLabel("Title:"))
        title_layout.addWidget(self.title_input)
        top_layout.addLayout(title_layout)

        # Add to Queue Button
        self.add_to_queue_button = QPushButton("Add to Upload Queue")
        self.add_to_queue_button.clicked.connect(self.add_to_queue)
        self.add_to_queue_button.setEnabled(False) # Initially disabled
        top_layout.addWidget(self.add_to_queue_button, alignment=Qt.AlignCenter)

        top_section_group.setLayout(top_layout)
        self.layout.addWidget(top_section_group)

    def _create_queue_section(self):
        queue_section_group = QGroupBox("Upload Queue")
        queue_main_layout = QVBoxLayout()

        self.upload_queue_listwidget = QListWidget()
        queue_main_layout.addWidget(self.upload_queue_listwidget)

        queue_buttons_layout = QHBoxLayout()
        self.remove_selected_button = QPushButton("Remove Selected Task")
        self.remove_selected_button.clicked.connect(self.remove_selected_task_from_queue)
        self.clear_completed_button = QPushButton("Clear Completed Tasks")
        self.clear_completed_button.clicked.connect(self.clear_completed_tasks_in_queue)

        queue_buttons_layout.addWidget(self.remove_selected_button)
        queue_buttons_layout.addWidget(self.clear_completed_button)
        queue_main_layout.addLayout(queue_buttons_layout)

        queue_section_group.setLayout(queue_main_layout)
        self.layout.addWidget(queue_section_group)

    def _create_log_section(self):
        log_section_group = QGroupBox("Logs")
        log_layout = QVBoxLayout()
        self.log_output_area = QTextEdit()
        self.log_output_area.setReadOnly(True)
        log_layout.addWidget(self.log_output_area)
        log_section_group.setLayout(log_layout)
        self.layout.addWidget(log_section_group)
        self.layout.setStretchFactor(log_section_group, 1)

    def _update_connection_status_indicator(self, connected, event_message=""):
        # connected: True (green), False (red), None (neutral/yellow for connecting)

        text_color = "black" # Default text color, might not be needed if circle is main indicator
        circle_char = "●"
        status_description = ""
        service_name = "tadreb.live"

        if connected is True:
            indicator_color_name = "green"
            status_description = f"Connected to {service_name}"
            if event_message: # If there's a specific success message like "user@instance"
                status_description = f"{event_message}" # Use it directly for now, will refine if needed
        elif connected is False:
            indicator_color_name = "red"
            status_description = f"Disconnected from {service_name}"
            if event_message:
                status_description = f"{service_name}: {event_message}"
        elif connected is None: # Intermediate state like "connecting"
            indicator_color_name = "orange"
            status_description = f"Connecting to {service_name}..."
            if event_message: # e.g. "Authenticating user..."
                 status_description = f"{event_message}"
        else: # Should not happen
            indicator_color_name = "grey"
            status_description = "Status Unknown"

        full_text = f"{circle_char} {status_description}"

        # Update the label's text and stylesheet
        if hasattr(self, 'connection_status_indicator_label'):
            self.connection_status_indicator_label.setText(full_text)
            # The stylesheet sets the color of the text. The circle character will inherit this color.
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
        if QThread.currentThread() == QApplication.instance().thread():
            self.log_output_area.append(message)
            print(message)
        else:
            self.gui_signals.emit_log(message)

    def show_status_message(self, message, timeout=3000):
        self.statusBar.showMessage(message, timeout)

    def _attempt_auto_connection(self):
        if not self.configured_instance_url or not self.configured_username or self.configured_password is None:
            self.log_message("Auto-connect failed: Missing instance URL, username, or password in configuration.")
            self._update_connection_status_indicator(False, "Configuration incomplete")
            self.show_status_message("Auto-connect failed: Configuration incomplete.", 5000)
            return

        self.log_message("Attempting automatic connection to the configured tadreb.live service...")
        self._update_connection_status_indicator(None, "Connecting...") # Generic message
        self.show_status_message(f"Attempting auto-connection to {self.configured_instance_url}...") # Keep URL here for status bar, but not in main log

        self._perform_connection_logic(self.configured_username, self.configured_password)

    def _perform_connection_logic(self, username, password):
        """
        Handles the actual logic of connecting, authenticating, and loading channels.
        Can be called by auto-connect or a manual connect button (if re-added).
        Assumes self.configured_instance_url is set.
        """
        if self.queue_manager and self.queue_manager.is_processing:
            # This check might be less relevant if auto-connect is only on startup
            # but good if we re-introduce a manual connect/reconnect.
            self.queue_manager.stop_processing()
            self.log_message("Stopped ongoing queue processing for (re)connection attempt.")

        self.peertube_client = PeerTubeClient(self.configured_instance_url)

        self.log_message(f"Authenticating user {username}...")
        self._update_connection_status_indicator(None, "Authenticating...") # Generic message
        self.show_status_message(f"Authenticating {username}...")

        if self.peertube_client.authenticate(username, password):
            self.log_message("Authentication successful!")
            self.show_status_message("Authentication successful!", 5000)
            self._update_connection_status_indicator(True) # Uses default "Connected to tadreb.live"


            if self.queue_manager:
                self.queue_manager.peertube_client = self.peertube_client # Update client for existing manager
                self.queue_manager.start_processing() # Resume processing if it was stopped
                self.log_message("Updated PeerTube client for existing QueueManager and restarted queue.")
            else:
                self.queue_manager = UploadQueueManager(
                    peertube_client=self.peertube_client,
                    status_update_callback=self.gui_signals.emit_task_update,
                    log_callback=self.gui_signals.emit_log,
                    upload_chunk_size_mb=4 # Explicitly pass chunk size, or let it use its default
                )
                self.log_message("UploadQueueManager initialized with 4MB chunk size.") # Log new chunk size
                # queue_manager.start_processing() will be called when a task is added if not already running.

            self.load_channels()
        else:
            self.log_message("Authentication failed. Check credentials in settings.py or server status.")
            self.show_status_message("Authentication failed.", 5000)
            # if hasattr(self, 'connection_status_label'): # Removed
            #     self.connection_status_label.setText("Connection Failed. Check settings/logs.") # Removed
            #     self.connection_status_label.setStyleSheet("color: red;") # Removed
            self._update_connection_status_indicator(False, "Connection Failed. Check logs.")
            self.peertube_client = None # Ensure client is None on failure
            # Potentially disable upload functionality here
            self.channel_combo.clear()
            self.channel_combo.addItem("Connection Failed")
            self.channel_combo.setEnabled(False)
            self.add_to_queue_button.setEnabled(False)


    def _on_channel_selection_change(self, index):
        # Enable "Add to Queue" only if a valid channel (not the placeholder) is selected
        if index > 0 and self.peertube_client and self.peertube_client.access_token: # Index 0 is "--- Select ---"
            self.add_to_queue_button.setEnabled(True)
        else:
            self.add_to_queue_button.setEnabled(False)

    def load_channels(self):
        if not self.peertube_client or not self.peertube_client.access_token:
            self.log_message("Cannot load channels: Not authenticated or client not initialized.")
            QMessageBox.warning(self, "Error", "Not authenticated. Please connect and authenticate first.")
            self.add_to_queue_button.setEnabled(False)
            return

        self.log_message("Loading channels...")
        self.show_status_message("Loading channels...")
        self.add_to_queue_button.setEnabled(False) # Disable while loading

        api_channels_data = self.peertube_client.get_channels()
        self.full_channel_list = [] # Reset full list
        self.user_channels = [] # Reset user_channels (which will be used by task update signal)

        if api_channels_data is not None:
            self.full_channel_list = api_channels_data # Store the raw list

            # Sort the full_channel_list by 'displayName', case-insensitive
            # We store this sorted list potentially in self.user_channels or use it directly for populating
            # For now, let's sort full_channel_list itself, or a copy if preferred.
            # The self.user_channels will be used by handle_task_update_signal, so it needs to be populated
            # with the items that are actually *in the dropdown* at any given time.
            # However, handle_task_update_signal iterates self.user_channels to find display names.
            # This implies self.user_channels should be the *complete* list of channel data used for display name lookup.

            # Let's keep self.full_channel_list as the master, sorted list from API.
            # And self.user_channels will be a copy of this, used by other parts of the code.
            # The actual QComboBox population will be handled by _populate_channel_combo
            # which will be called by search later.

            if self.full_channel_list:
                # Sort the raw list fetched from API to be our definitive full_channel_list
                self.full_channel_list.sort(key=lambda ch: ch['displayName'].lower())

                # Populate self.user_channels which is used by handle_task_update_signal for display name lookups
                # This should be a copy of the full list of channel details.
                self.user_channels = list(self.full_channel_list)

                self.log_message(f"Loaded and sorted {len(self.full_channel_list)} channels.")
                self.show_status_message(f"Loaded {len(self.full_channel_list)} channels.", 3000)
                # Initial population of the combo box without any search term
                self._populate_channel_combo(self.full_channel_list)
            else:
                self.log_message("No channels found for your account or instance.")
                self._populate_channel_combo([]) # Populate with empty to show "No channels"
                self.show_status_message("No channels found.", 3000)
        else: # api_channels_data is None (error during fetch)
            self.log_message("Failed to load channels. See logs.")
            self._populate_channel_combo(None) # Populate with None to show "Failed to load"
            QMessageBox.critical(self, "Error", "Failed to load channels from the PeerTube instance.")
            self.show_status_message("Failed to load channels.", 3000)

        # Call this to set initial state of add_to_queue_button based on current selection
        # This will be handled by _populate_channel_combo or _on_channel_selection_change
        # self._on_channel_selection_change(self.channel_combo.currentIndex())
        # If _populate_channel_combo enables/disables combo and calls _on_channel_selection_change, this is fine.

    def _on_channel_search_changed(self, search_text):
        """
        Filters the channel list in the QComboBox based on the search_text.
        """
        if not hasattr(self, 'full_channel_list') or not self.full_channel_list:
            # No channels loaded yet, or list is empty
            self._populate_channel_combo(self.full_channel_list) # Show appropriate message like "No channels" or "Failed to load"
            return

        search_text_lower = search_text.lower().strip()

        if not search_text_lower:
            # Search is empty, show all (sorted) channels
            self._populate_channel_combo(self.full_channel_list)
            return

        filtered_channels = [
            ch for ch in self.full_channel_list
            if search_text_lower in ch['displayName'].lower() or \
               search_text_lower in ch['name'].lower()
        ]

        # The full_channel_list is already sorted. Filtering preserves relative order.
        # If a different sort order was needed for filtered results, it would be applied here.
        self._populate_channel_combo(filtered_channels)


    def _populate_channel_combo(self, channels_to_display):
        """
        Helper function to populate the channel_combo QComboBox.
        channels_to_display: A list of channel dictionaries to display.
                             If None, indicates a failure to load.
                             If empty list, indicates no channels found.
        """
        self.channel_combo.clear()
        self.add_to_queue_button.setEnabled(False) # Disable by default

        if channels_to_display is None: # Error case
            self.channel_combo.addItem("Failed to load channels")
            self.channel_combo.setEnabled(False)
        elif not channels_to_display: # No channels found
            self.channel_combo.addItem("No channels found")
            self.channel_combo.setEnabled(False)
        else: # Channels available
            self.channel_combo.addItem("--- Select a Channel ---")
            for channel in channels_to_display:
                display_text = f"{channel['displayName']} (Handle: {channel['name']})"
                owner_display = channel.get('ownerAccountName', 'N/A')
                is_own_channel = False
                if self.peertube_client and self.peertube_client.username:
                    if owner_display == self.peertube_client.username or \
                       owner_display.startswith(self.peertube_client.username + "@"):
                       is_own_channel = True

                if (self.peertube_client and self.peertube_client.user_role_id in [0, 1]) and not is_own_channel and owner_display != 'N/A':
                    display_text += f" (Owner: {owner_display})"
                self.channel_combo.addItem(display_text, channel['id'])
            self.channel_combo.setEnabled(True)

        # Ensure the "Add to Queue" button state is updated based on the current selection (or lack thereof)
        self._on_channel_selection_change(self.channel_combo.currentIndex())


    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select Video File", "", "Video Files (*.mp4 *.avi *.mkv *.mov *.webm);;All Files (*)")
        if file_name:
            self.file_path_input.setText(file_name)
            self.log_message(f"Selected file: {file_name}")
            self.show_status_message(f"File selected: {os.path.basename(file_name)}", 2000)

    def add_to_queue(self):
        if not self.queue_manager:
            QMessageBox.warning(self, "Not Connected", "Please connect and authenticate to a PeerTube instance first.")
            self.log_message("Error: Cannot add to queue, QueueManager not initialized (not connected).")
            return

        file_path = self.file_path_input.text()
        title = self.title_input.text().strip()
        current_channel_index = self.channel_combo.currentIndex()

        if not file_path:
            QMessageBox.warning(self, "Input Error", "Please select a video file.")
            return

        if current_channel_index <= 0:
            QMessageBox.warning(self, "Input Error", "Please select a channel.")
            return

        channel_id = self.channel_combo.itemData(current_channel_index)

        if not title:
            QMessageBox.warning(self, "Input Error", "Please enter a video title.")
            return
        if not (3 <= len(title) <= 120):
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

    def handle_task_update_signal(self, task_id, status_enum, progress, video_id, error_message, is_new, file_path, title, channel_id_from_cb, is_removed):
        if is_removed:
            if task_id in self.task_widgets:
                item_to_remove = self.task_widgets.pop(task_id)
                self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(item_to_remove))
                self.log_message(f"Removed task {task_id} from GUI.")
            return

        item = self.task_widgets.get(task_id)

        if is_new and not item:
            channel_display_name = "Unknown Channel"
            for ch_data in self.user_channels: # Use self.user_channels which is populated
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
            self.task_widgets[task_id] = item # Store the QListWidgetItem itself
            self.upload_queue_listwidget.addItem(item)

            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 2, 5, 2)

            # Store custom widget parts in a dictionary associated with the task_id or QListWidgetItem
            # For simplicity, let's make task_widgets store a dict of these parts
            self.task_widgets[task_id] = {
                'item': item, # Keep reference to the QListWidgetItem
                'label': QLabel(base_text),
                'progress_bar': QProgressBar(),
                'status_label': QLabel(f" {status_enum.value}")
            }

            self.task_widgets[task_id]['label'].setWordWrap(True)
            item_layout.addWidget(self.task_widgets[task_id]['label'], 1)

            self.task_widgets[task_id]['progress_bar'].setRange(0, 100)
            self.task_widgets[task_id]['progress_bar'].setValue(0)
            self.task_widgets[task_id]['progress_bar'].setTextVisible(True)
            self.task_widgets[task_id]['progress_bar'].setFixedSize(120, 18)
            item_layout.addWidget(self.task_widgets[task_id]['progress_bar'])

            self.task_widgets[task_id]['status_label'].setFixedWidth(80)
            item_layout.addWidget(self.task_widgets[task_id]['status_label'])

            item_widget.setLayout(item_layout)
            item.setSizeHint(item_widget.sizeHint())
            self.upload_queue_listwidget.setItemWidget(item, item_widget)
            self.log_message(f"Added task {task_id} to GUI queue: {title}")

        # Check if item and its custom widget parts exist before updating
        if task_id in self.task_widgets and isinstance(self.task_widgets[task_id], dict) and 'label' in self.task_widgets[task_id]:
            task_gui_parts = self.task_widgets[task_id]
            task_gui_parts['status_label'].setText(f" {status_enum.value}")
            task_gui_parts['progress_bar'].setValue(progress if progress is not None else 0)

            current_label_text = task_gui_parts['label'].text()
            # Avoid re-appending Video ID if already there
            video_id_text_segment = f" (Video ID: {video_id})"

            if status_enum == TaskStatus.FAILED:
                task_gui_parts['label'].setStyleSheet("color: red;")
                task_gui_parts['status_label'].setStyleSheet("color: red;")
                self.log_message(f"Task {task_id} ('{title}') FAILED: {error_message}") # Use title from signal for log
            elif status_enum == TaskStatus.COMPLETED:
                task_gui_parts['label'].setStyleSheet("color: green;")
                task_gui_parts['status_label'].setStyleSheet("color: green;")
                if video_id and video_id_text_segment not in current_label_text:
                    task_gui_parts['label'].setText(current_label_text + video_id_text_segment)
            elif status_enum == TaskStatus.CANCELLED:
                task_gui_parts['label'].setStyleSheet("color: orange;")
                task_gui_parts['status_label'].setStyleSheet("color: orange;")
            else: # Pending, Initializing, Uploading
                task_gui_parts['label'].setStyleSheet("")
                task_gui_parts['status_label'].setStyleSheet("")
                # Ensure Video ID is not present if not completed
                if video_id_text_segment in current_label_text:
                     task_gui_parts['label'].setText(current_label_text.replace(video_id_text_segment, ""))


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
                else: # Not found in manager, but was in GUI dict. Remove from GUI.
                    self.log_message(f"Task {task_id_to_remove} not in queue manager, removing from GUI only.")
                    if task_id_to_remove in self.task_widgets:
                        popped_item_data = self.task_widgets.pop(task_id_to_remove)
                        if isinstance(popped_item_data, dict):
                             self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(popped_item_data['item']))
                        self.log_message(f"Removed task {task_id_to_remove} from GUI.")
        elif not self.queue_manager:
             self.log_message("Queue manager not available to remove task.")


    def clear_completed_tasks_in_queue(self):
        if not self.queue_manager:
            self.log_message("Queue manager not available to clear tasks.")
            return

        confirm = QMessageBox.question(self, "Confirm Clear",
                                       "Are you sure you want to remove all COMPLETED tasks from the list?",
                                       QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            tasks_to_remove_ids = []
            # Need to iterate based on what's in the GUI and its status, then tell manager
            for task_id, widget_data in list(self.task_widgets.items()): # list() for safe iteration if removing
                if isinstance(widget_data, dict) and widget_data['status_label'].text().strip() == TaskStatus.COMPLETED.value:
                    tasks_to_remove_ids.append(task_id)

            if not tasks_to_remove_ids:
                self.log_message("No completed tasks found in the GUI list to clear.")
                return

            for task_id in tasks_to_remove_ids:
                # Manager will emit signal which will remove it from GUI via handle_task_update_signal
                self.queue_manager.remove_task(task_id)
            self.log_message(f"Requested removal of {len(tasks_to_remove_ids)} completed tasks.")


    def closeEvent(self, event):
        self.log_message("Main window closing. Stopping queue manager...")
        if self.queue_manager:
            self.queue_manager.stop_processing()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())
