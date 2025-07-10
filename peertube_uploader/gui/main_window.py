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
        self.user_channels = []
        self.task_widgets = {}

        self.gui_signals = GuiSignalEmitter()
        self.gui_signals.task_update_signal.connect(self.handle_task_update_signal)
        self.gui_signals.log_signal.connect(self.log_message_from_thread)

        self._create_top_section()
        self._create_queue_section()
        self._create_log_section()
        self._create_status_bar()

        self.log_message(f"Application started. Configured for instance: {self.configured_instance_url if self.configured_instance_url else 'NOT CONFIGURED'}")

    def _create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.show_status_message("Ready")

    def _create_top_section(self):
        top_section_group = QGroupBox("Video Details & Connection")
        top_layout = QVBoxLayout()

        # Instance URL input is removed. Button text will show the configured URL.
        if self.configured_instance_url:
            connect_button_text = f"Connect & Authenticate to: {self.configured_instance_url}"
        else:
            connect_button_text = "Connect & Authenticate (URL NOT CONFIGURED)"

        self.connect_button = QPushButton(connect_button_text)
        self.connect_button.clicked.connect(self.connect_and_authenticate)
        if not self.configured_instance_url:
            self.connect_button.setEnabled(False) # Disable if no URL
        top_layout.addWidget(self.connect_button)

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

        # Channel Selection
        channel_layout = QHBoxLayout()
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Connect to instance first")
        self.channel_combo.setEnabled(False)
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

    def connect_and_authenticate(self):
        # Instance URL is now from self.configured_instance_url
        if not self.configured_instance_url:
            QMessageBox.critical(self, "Configuration Error", "PeerTube instance URL is not configured. Cannot connect.")
            self.log_message("Error: Connection attempt failed, instance URL not configured.")
            return

        instance_url = self.configured_instance_url # Use the configured URL

        if self.queue_manager and self.queue_manager.is_processing:
            self.queue_manager.stop_processing()
            self.log_message("Stopped previous queue processing due to new connection attempt.")

        self.peertube_client = PeerTubeClient(instance_url) # Use configured URL
        self.log_message(f"Attempting to connect to {instance_url}...")
        self.show_status_message(f"Connecting to {instance_url}...")

        username, ok1 = QInputDialog.getText(self, "Login", f"Enter Username for {instance_url}:")
        if not ok1 or not username:
            self.log_message("Authentication cancelled by user (username).")
            self.show_status_message("Authentication cancelled.")
            return

        password, ok2 = QInputDialog.getText(self, "Login", "Enter Password:", QLineEdit.Password)
        if not ok2:
            self.log_message("Authentication cancelled by user (password).")
            self.show_status_message("Authentication cancelled.")
            return

        self.log_message(f"Authenticating user {username}...")
        self.show_status_message(f"Authenticating {username}...")

        if self.peertube_client.authenticate(username, password):
            self.log_message("Authentication successful!")
            self.show_status_message("Authentication successful!", 5000)

            if self.queue_manager:
                 self.queue_manager.stop_processing() # Stop old one if any
                 # Update existing queue manager's client if it exists and is processing, or re-init
                 self.queue_manager.peertube_client = self.peertube_client
                 self.log_message("Updated PeerTube client for existing QueueManager.")
            else:
                self.queue_manager = UploadQueueManager(
                    peertube_client=self.peertube_client,
                    status_update_callback=self.gui_signals.emit_task_update,
                    log_callback=self.gui_signals.emit_log
                )
                self.log_message("UploadQueueManager initialized.")
            self.load_channels()
        else:
            self.log_message("Authentication failed. Check logs and credentials.")
            QMessageBox.critical(self, "Authentication Failed", "Could not authenticate. Please check credentials and logs.")
            self.show_status_message("Authentication failed.", 5000)
            self.peertube_client = None
            # Do not nullify queue_manager here, it might have pending tasks from a previous session if we implement saving queue state

    def load_channels(self):
        if not self.peertube_client or not self.peertube_client.access_token:
            self.log_message("Cannot load channels: Not authenticated.")
            QMessageBox.warning(self, "Error", "Not authenticated. Please connect and authenticate first.")
            return

        self.log_message("Loading channels...")
        self.show_status_message("Loading channels...")
        channels_data = self.peertube_client.get_channels()

        self.channel_combo.clear()
        self.user_channels = []

        if channels_data is not None:
            if channels_data:
                self.channel_combo.addItem("--- Select a Channel ---")
                for channel in channels_data:
                    self.user_channels.append({
                        'id': channel['id'],
                        'displayName': channel['displayName'],
                        'name': channel['name'],
                        'ownerAccountName': channel.get('ownerAccountName', 'N/A')
                    })

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
                self.log_message(f"Loaded {len(channels_data)} channels.")
                self.show_status_message(f"Loaded {len(channels_data)} channels.", 3000)
            else:
                self.log_message("No channels found for your account or instance.")
                self.channel_combo.addItem("No channels found")
                self.channel_combo.setEnabled(False)
                self.show_status_message("No channels found.", 3000)
        else:
            self.log_message("Failed to load channels. See logs.")
            self.channel_combo.addItem("Failed to load channels")
            self.channel_combo.setEnabled(False)
            QMessageBox.critical(self, "Error", "Failed to load channels from the PeerTube instance.")
            self.show_status_message("Failed to load channels.", 3000)

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
