import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog,
                             QComboBox, QGroupBox, QTextEdit, QListWidget, QListWidgetItem,
                             QInputDialog, QMessageBox, QStatusBar, QProgressBar,
                             QSplitter, QFrame) # Added QSplitter and QFrame
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
        self.video_details_group = self._create_top_section()
        self.queue_section_group = self._create_queue_section()

        self.main_v_splitter = QSplitter(Qt.Vertical)
        self.main_v_splitter.addWidget(self.log_section_group)

        self.middle_h_splitter = QSplitter(Qt.Horizontal)
        self.middle_h_splitter.addWidget(self.video_details_group)

        self.channel_browser_group = QGroupBox("Available Channels")
        channel_browser_layout = QVBoxLayout()
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
        details_width = int(total_width * 0.40)
        placeholder_width = int(total_width * 0.60) # For channel browser
        self.middle_h_splitter.setSizes([details_width, placeholder_width])

        self.overall_layout.addWidget(self.main_v_splitter)
        self._create_status_bar()

        self.log_message(f"Application started. Configured endpoint: {'Provided' if self.configured_instance_url else 'Not Provided'}.")

        if self.configured_instance_url and self.configured_username:
            self._attempt_auto_connection()
        elif not self.configured_instance_url:
            self.log_message("Auto-connect skipped: Instance URL not configured.")
            self._update_connection_status_indicator(False, "Instance URL not configured")
        else:
            self.log_message("Auto-connect skipped: Username not configured in settings.py.")
            self._update_connection_status_indicator(False, "Username not configured")

        self._update_add_to_queue_button_state()
        self._update_queue_control_button_states()


    def _create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.connection_status_indicator_label = QLabel("● Initializing...")
        self.connection_status_indicator_label.setStyleSheet("padding-left: 5px; padding-right: 5px; color: orange;")
        self.statusBar.addWidget(self.connection_status_indicator_label)


    def _create_top_section(self):
        top_section_group = QGroupBox("Video Details")
        top_layout = QVBoxLayout()

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

        search_layout = QHBoxLayout()
        self.channel_search_input = QLineEdit()
        self.channel_search_input.setPlaceholderText("Search channels by name or handle...")
        self.channel_search_input.textChanged.connect(self._on_channel_search_changed)
        search_layout.addWidget(QLabel("Search Channel:"))
        search_layout.addWidget(self.channel_search_input)
        top_layout.addLayout(search_layout)

        title_layout = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter video title (3-120 characters)")
        self.title_input.textChanged.connect(self._update_add_to_queue_button_state)
        title_layout.addWidget(QLabel("Title:"))
        title_layout.addWidget(self.title_input)
        top_layout.addLayout(title_layout)

        self.add_to_queue_button = QPushButton("Add to Upload Queue")
        self.add_to_queue_button.clicked.connect(self.add_to_queue)
        self.add_to_queue_button.setEnabled(False)
        top_layout.addWidget(self.add_to_queue_button, alignment=Qt.AlignCenter)

        top_section_group.setLayout(top_layout)
        return top_section_group

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
            self._update_add_to_queue_button_state()

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

        if task_id in self.task_widgets and isinstance(self.task_widgets[task_id], dict) and 'label' in self.task_widgets[task_id]:
            task_gui_parts = self.task_widgets[task_id]
            task_gui_parts['status_label'].setText(f" {status_enum.value}")
            task_gui_parts['progress_bar'].setValue(progress if progress is not None else 0)

            current_label_text = task_gui_parts['label'].text()
            video_id_text_segment = f" (Video ID: {video_id})"

            if status_enum == TaskStatus.FAILED:
                task_gui_parts['label'].setStyleSheet("color: red;")
                task_gui_parts['status_label'].setStyleSheet("color: red;")
                self.log_message(f"Task {task_id} ('{title}') FAILED: {error_message}")
            elif status_enum == TaskStatus.COMPLETED:
                task_gui_parts['label'].setStyleSheet("color: green;")
                task_gui_parts['status_label'].setStyleSheet("color: green;")
                if video_id and video_id_text_segment not in current_label_text:
                    task_gui_parts['label'].setText(current_label_text + video_id_text_segment)
            elif status_enum == TaskStatus.CANCELLED:
                task_gui_parts['label'].setStyleSheet("color: orange;")
                task_gui_parts['status_label'].setStyleSheet("color: orange;")
            else:
                task_gui_parts['label'].setStyleSheet("")
                task_gui_parts['status_label'].setStyleSheet("")
                if video_id_text_segment in current_label_text:
                     task_gui_parts['label'].setText(current_label_text.replace(video_id_text_segment, ""))

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
        self.log_message("Main window closing. Stopping queue manager...")
        if self.queue_manager:
            self.queue_manager.stop_processing()
        super().closeEvent(event)

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


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())
