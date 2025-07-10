import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog,
                             QComboBox, QGroupBox, QTextEdit, QListWidget, QListWidgetItem,
                             QInputDialog, QMessageBox, QStatusBar, QProgressBar)
from PyQt5.QtCore import Qt, pyqtSignal, QObject # Import pyqtSignal and QObject for custom signals
from api.peertube_client import PeerTubeClient
from core.queue_manager import UploadQueueManager, TaskStatus # Import UploadQueueManager and TaskStatus

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
        self.setWindowTitle("PeerTube Uploader")
        self.setGeometry(100, 100, 900, 750) # Increased height slightly for progress bars

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.peertube_client = None
        self.queue_manager = None # Initialize later
        self.user_channels = []
        self.task_widgets = {} # task_id -> QListWidgetItem

        # Setup signal emitter for thread-safe GUI updates
        self.gui_signals = GuiSignalEmitter()
        self.gui_signals.task_update_signal.connect(self.handle_task_update_signal)
        self.gui_signals.log_signal.connect(self.log_message_from_thread)


        self._create_top_section()
        self._create_queue_section()
        self._create_log_section()
        self._create_status_bar()

        self.log_message("Application started. Please connect to your PeerTube instance.")

    def _create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.show_status_message("Ready")

    def _create_top_section(self):
        top_section_group = QGroupBox("Video Details & Connection")
        top_layout = QVBoxLayout()

        instance_layout = QHBoxLayout()
        instance_label = QLabel("PeerTube Instance URL:")
        self.instance_url_input = QLineEdit()
        self.instance_url_input.setPlaceholderText("https://peertube.example.com")
        # self.instance_url_input.setText("http://localhost:9000") # For local testing
        instance_layout.addWidget(instance_label)
        instance_layout.addWidget(self.instance_url_input)
        top_layout.addLayout(instance_layout)

        self.connect_button = QPushButton("Connect & Authenticate")
        self.connect_button.clicked.connect(self.connect_and_authenticate)
        top_layout.addWidget(self.connect_button)

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

        channel_layout = QHBoxLayout()
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Connect to instance first")
        self.channel_combo.setEnabled(False)
        channel_layout.addWidget(QLabel("Channel:"))
        channel_layout.addWidget(self.channel_combo, 1)
        top_layout.addLayout(channel_layout)

        title_layout = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter video title")
        title_layout.addWidget(QLabel("Title:"))
        title_layout.addWidget(self.title_input)
        top_layout.addLayout(title_layout)

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

        # Buttons for queue management
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
        # This method is connected to the signal and is safe to call from other threads
        self.log_output_area.append(message)
        print(message)

    def log_message(self, message):
        # For messages logged directly from the GUI thread
        if QThread.currentThread() == QApplication.instance().thread():
            self.log_output_area.append(message)
            print(message)
        else:
            # If somehow called from another thread without direct signal, emit it
            self.gui_signals.emit_log(message)


    def show_status_message(self, message, timeout=3000):
        self.statusBar.showMessage(message, timeout)

    def connect_and_authenticate(self):
        instance_url = self.instance_url_input.text().strip()
        if not instance_url:
            QMessageBox.warning(self, "Input Error", "Please enter a PeerTube instance URL.")
            self.log_message("Error: PeerTube instance URL is required.")
            return

        # Stop existing queue manager if any, before creating new client
        if self.queue_manager and self.queue_manager.is_processing:
            self.queue_manager.stop_processing()
            self.log_message("Stopped previous queue processing due to new connection attempt.")

        self.peertube_client = PeerTubeClient(instance_url)
        self.log_message(f"Attempting to connect to {instance_url}...")
        self.show_status_message(f"Connecting to {instance_url}...")

        username, ok1 = QInputDialog.getText(self, "Login", "Enter your PeerTube Username:")
        if not ok1 or not username:
            self.log_message("Authentication cancelled by user (username).")
            self.show_status_message("Authentication cancelled.")
            return

        password, ok2 = QInputDialog.getText(self, "Login", "Enter your PeerTube Password:", QLineEdit.Password)
        if not ok2: # Allow empty password field if user cancels
            self.log_message("Authentication cancelled by user (password).")
            self.show_status_message("Authentication cancelled.")
            return

        self.log_message(f"Authenticating user {username}...")
        self.show_status_message(f"Authenticating {username}...")

        # Pass the GUI log method to the client for its internal logging
        # self.peertube_client.set_logger(self.log_message_from_thread) # If client supports this

        if self.peertube_client.authenticate(username, password):
            self.log_message("Authentication successful!")
            self.show_status_message("Authentication successful!", 5000)

            # Initialize QueueManager here, after successful authentication
            if self.queue_manager: # Stop old one if it exists
                 self.queue_manager.stop_processing()
            self.queue_manager = UploadQueueManager(
                peertube_client=self.peertube_client,
                status_update_callback=self.gui_signals.emit_task_update, # Use signal emitter
                log_callback=self.gui_signals.emit_log
            )
            self.log_message("UploadQueueManager initialized.")
            self.load_channels()
        else:
            self.log_message("Authentication failed. Check logs and credentials.")
            QMessageBox.critical(self, "Authentication Failed", "Could not authenticate. Please check credentials, instance URL, and logs.")
            self.show_status_message("Authentication failed.", 5000)
            self.peertube_client = None
            self.queue_manager = None


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
                    self.user_channels.append({'id': channel['id'], 'displayName': channel['displayName'], 'name': channel['name']})
                    self.channel_combo.addItem(f"{channel['displayName']} (Handle: {channel['name']})", channel['id']) # Store ID as item data
                self.channel_combo.setEnabled(True)
                self.log_message(f"Loaded {len(channels_data)} channels.")
                self.show_status_message(f"Loaded {len(channels_data)} channels.", 3000)
            else:
                self.log_message("No channels found for your account.")
                self.channel_combo.addItem("No channels found")
                self.channel_combo.setEnabled(False)
                self.show_status_message("No channels found for your account.", 3000)
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

        if current_channel_index <= 0: # 0 is "--- Select ---"
            QMessageBox.warning(self, "Input Error", "Please select a channel.")
            return

        channel_id = self.channel_combo.itemData(current_channel_index) # Get stored ID

        if not title:
            QMessageBox.warning(self, "Input Error", "Please enter a video title.")
            return

        # Description, privacy, etc., can be added as more fields or defaults
        task_id = self.queue_manager.add_task(
            file_path=file_path,
            channel_id=channel_id,
            title=title
            # TODO: Add description, privacy, nsfw, tags from GUI if elements are added
        )
        self.log_message(f"Task '{title}' (ID: {task_id}) added to internal queue.")
        self.show_status_message(f"Added '{title}' to queue.", 2000)

        self.file_path_input.clear()
        self.title_input.clear()

    def handle_task_update_signal(self, task_id, status_enum, progress, video_id, error_message, is_new, file_path, title, channel_id_from_cb, is_removed):
        # This method is connected to the signal and is safe to call from other threads
        if is_removed:
            if task_id in self.task_widgets:
                item_to_remove = self.task_widgets.pop(task_id)
                self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(item_to_remove))
                self.log_message(f"Removed task {task_id} from GUI.")
            return

        item = self.task_widgets.get(task_id)

        if is_new and not item:
            # Find the channel display name for the new item
            channel_display_name = "Unknown Channel"
            for ch_data in self.user_channels:
                if ch_data['id'] == channel_id_from_cb:
                    channel_display_name = ch_data['displayName']
                    break

            base_text = f"ID: {task_id} - Title: {title} - Channel: {channel_display_name}"
            item = QListWidgetItem()
            self.task_widgets[task_id] = item
            self.upload_queue_listwidget.addItem(item)

            # Create a custom widget for the list item to include a progress bar
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 2, 5, 2) # Compact margins

            self.task_widgets[task_id].label = QLabel(base_text)
            self.task_widgets[task_id].label.setWordWrap(True)
            item_layout.addWidget(self.task_widgets[task_id].label, 1) # Label takes expanding space

            self.task_widgets[task_id].progress_bar = QProgressBar()
            self.task_widgets[task_id].progress_bar.setRange(0, 100)
            self.task_widgets[task_id].progress_bar.setValue(0)
            self.task_widgets[task_id].progress_bar.setTextVisible(True)
            self.task_widgets[task_id].progress_bar.setFixedSize(120, 18) # Fixed size for progress bar
            item_layout.addWidget(self.task_widgets[task_id].progress_bar)

            self.task_widgets[task_id].status_label = QLabel(f" {status_enum.value}")
            self.task_widgets[task_id].status_label.setFixedWidth(80) # Fixed width for status
            item_layout.addWidget(self.task_widgets[task_id].status_label)

            item_widget.setLayout(item_layout)
            item.setSizeHint(item_widget.sizeHint())
            self.upload_queue_listwidget.setItemWidget(item, item_widget)
            self.log_message(f"Added task {task_id} to GUI queue: {title}")


        if item and hasattr(self.task_widgets[task_id], 'label'): # Check if custom widget setup
            # Update existing item
            self.task_widgets[task_id].status_label.setText(f" {status_enum.value}")
            self.task_widgets[task_id].progress_bar.setValue(progress if progress is not None else 0)

            if status_enum == TaskStatus.FAILED:
                self.task_widgets[task_id].label.setStyleSheet("color: red;")
                self.task_widgets[task_id].status_label.setStyleSheet("color: red;")
                self.log_message(f"Task {task_id} ('{self.task_widgets[task_id].label.text().split(' - ')[1]}') FAILED: {error_message}")
            elif status_enum == TaskStatus.COMPLETED:
                self.task_widgets[task_id].label.setStyleSheet("color: green;")
                self.task_widgets[task_id].status_label.setStyleSheet("color: green;")
                self.task_widgets[task_id].label.setText(self.task_widgets[task_id].label.text() + f" (Video ID: {video_id})")
            elif status_enum == TaskStatus.CANCELLED:
                self.task_widgets[task_id].label.setStyleSheet("color: orange;")
                self.task_widgets[task_id].status_label.setStyleSheet("color: orange;")
            else:
                self.task_widgets[task_id].label.setStyleSheet("") # Reset color
                self.task_widgets[task_id].status_label.setStyleSheet("")


    def remove_selected_task_from_queue(self):
        selected_items = self.upload_queue_listwidget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "No Selection", "Please select a task from the queue to remove.")
            return

        list_item_to_remove = selected_items[0] # QListWidget is single-selection by default

        # Find task_id associated with this QListWidgetItem
        task_id_to_remove = None
        for tid, item_widget_data in self.task_widgets.items():
            if item_widget_data == list_item_to_remove: # Comparing QListWidgetItem references
                task_id_to_remove = tid
                break

        if task_id_to_remove and self.queue_manager:
            confirm = QMessageBox.question(self, "Confirm Remove",
                                           f"Are you sure you want to remove task ID {task_id_to_remove}?",
                                           QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                if self.queue_manager.remove_task(task_id_to_remove):
                    self.log_message(f"Request to remove task {task_id_to_remove} sent to queue manager.")
                    # GUI removal will happen via handle_task_update_signal with is_removed=True
                else:
                    self.log_message(f"Failed to send remove request for task {task_id_to_remove} to queue manager (task not found in manager).")
                    # If not found in manager, but in GUI, remove from GUI directly
                    if task_id_to_remove in self.task_widgets:
                         item_to_remove_gui = self.task_widgets.pop(task_id_to_remove)
                         self.upload_queue_listwidget.takeItem(self.upload_queue_listwidget.row(item_to_remove_gui))
                         self.log_message(f"Removed task {task_id_to_remove} from GUI (was not in manager).")

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
            with self.queue_manager.queue_lock: # Iterate over a copy for safe removal
                for task_obj in list(self.queue_manager.queue):
                    if task_obj.status == TaskStatus.COMPLETED:
                        tasks_to_remove_ids.append(task_obj.task_id)

            for task_id in tasks_to_remove_ids:
                self.queue_manager.remove_task(task_id) # This will trigger GUI update via signal
            self.log_message(f"Requested removal of {len(tasks_to_remove_ids)} completed tasks.")


    def closeEvent(self, event):
        # Ensure queue processing is stopped cleanly when window closes
        self.log_message("Main window closing. Stopping queue manager...")
        if self.queue_manager:
            self.queue_manager.stop_processing()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())
