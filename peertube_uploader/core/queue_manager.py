import time
import threading
from enum import Enum
import os
import concurrent.futures # Added for ThreadPoolExecutor

from api.peertube_client import PeerTubeClient


class TaskStatus(Enum):
    PENDING = "Pending"
    UPLOADING = "Uploading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    INITIALIZING = "Initializing" # For resumable upload init phase

class VideoUploadTask:
    def __init__(self, task_id, file_path, channel_id, title, description="", privacy=1, nsfw=False, tags=None):
        self.task_id = task_id
        self.file_path = file_path
        self.channel_id = channel_id
        self.title = title
        self.description = description
        self.privacy = privacy
        self.nsfw = nsfw
        self.tags = tags if tags else []

        self.status = TaskStatus.PENDING
        self.progress = 0  # Percentage or bytes uploaded
        self.error_message = None
        self.video_id_on_peertube = None # Store the ID/UUID of the video once created/uploaded
        self.resumable_upload_id = None # For TUS/resumable uploads
        self.total_size = 0
        self.bytes_uploaded = 0


class UploadQueueManager:
    def __init__(self, peertube_client: PeerTubeClient, status_update_callback=None, log_callback=None, max_concurrent_uploads=2):
        self.queue = []
        self.peertube_client = peertube_client
        # self.current_task = None # Replaced by multiple active tasks logic
        self.active_tasks = {} # task_id: future - To keep track of tasks currently in ThreadPoolExecutor
        self.is_processing = False
        self.stop_event = threading.Event()
        self.queue_lock = threading.Lock() # Protects self.queue and self.active_tasks
        self.task_id_counter = 0
        # self.processing_thread = None # Replaced by ThreadPoolExecutor
        self.executor = None
        self.max_concurrent_uploads = max_concurrent_uploads

        # Callbacks to update GUI or log messages
        self.status_update_callback = status_update_callback # func(task_id, status, progress, video_id_on_peertube, error_message)
        self.log_callback = log_callback # func(message)


    def _log(self, message):
        if self.log_callback:
            self.log_callback(f"[QueueManager] {message}")
        else:
            print(f"[QueueManager] {message}")

    def _update_task_status(self, task: VideoUploadTask, status: TaskStatus, progress=None, video_id=None, error=None):
        task.status = status
        if progress is not None:
            task.progress = progress
        if video_id is not None:
            task.video_id_on_peertube = video_id
        if error is not None:
            task.error_message = error

        self._log(f"Task '{task.title}' (ID: {task.task_id}) status: {status.value}, Progress: {task.progress}%, Error: {task.error_message}")

        if self.status_update_callback:
            # For a general status update, is_new and is_removed are False.
            # We need to pass all arguments the signal expects.
            self.status_update_callback(
                task.task_id, task.status, task.progress,
                task.video_id_on_peertube, task.error_message,
                False,  # is_new
                task.file_path, # file_path
                task.title,     # title
                task.channel_id,# channel_id
                False   # is_removed
            )


    def add_task(self, file_path, channel_id, title, description="", privacy=1, nsfw=False, tags=None):
        with self.queue_lock:
            self.task_id_counter += 1
            task = VideoUploadTask(self.task_id_counter, file_path, channel_id, title, description, privacy, nsfw, tags)
            self.queue.append(task)
            self._log(f"Added task: {title} (ID: {task.task_id}) to queue. File: {file_path}")
            if self.status_update_callback: # Notify GUI about new task in queue
                 self.status_update_callback(task.task_id, task.status, task.progress, None, None, is_new=True, file_path=task.file_path, title=task.title, channel_id=task.channel_id, is_removed=False)

        if not self.is_processing: # If not already running, start it.
            self.start_processing()
        else: # If already running, explicitly try to schedule tasks.
            self._schedule_tasks()
        return task.task_id

    def start_processing(self):
        if self.is_processing and self.executor:
            self._log("Processing already started.")
            return

        if not self.peertube_client or not self.peertube_client.access_token:
            self._log("Cannot start processing: PeerTube client not authenticated.")
            return

        self.is_processing = True
        self.stop_event.clear()
        if not self.executor: # Create executor if it doesn't exist or was shut down
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrent_uploads)

        self._log(f"Started queue processing with up to {self.max_concurrent_uploads} concurrent uploads.")
        self._schedule_tasks() # Initial scheduling of tasks

    def stop_processing(self):
        self._log("Stopping queue processing...")
        self.is_processing = False # Prevent new tasks from being scheduled by _schedule_tasks
        self.stop_event.set()      # Signal tasks currently in _handle_upload to stop

        if self.executor:
            # It's important to not hold the lock while calling future.cancel() or waiting for shutdown,
            # as done_callbacks (like _task_done_callback) might need the lock.

            # Get a list of futures to attempt to cancel.
            futures_to_cancel = []
            with self.queue_lock:
                for task_id, future_item in self.active_tasks.items():
                    # Check if future is not done before trying to cancel
                    if not future_item.done():
                         futures_to_cancel.append((task_id, future_item))

            for task_id, future in futures_to_cancel:
                if future.cancel(): # Returns True if future was indeed cancelled
                    self._log(f"Task {task_id} was pending in executor and has been cancelled.")
                    # The _task_done_callback will be triggered for this cancelled future
                    # and will update the task status appropriately.
                else:
                    # If cancel() returned False, the task might be running or already done.
                    # Running tasks need to check self.stop_event.
                     if not future.done(): # Check again, as it might have finished quickly
                        self._log(f"Task {task_id} could not be cancelled (likely already running or completed). Relies on stop_event.")

            # Wait for all tasks to complete their execution or acknowledge cancellation.
            # Tasks that were successfully cancelled by future.cancel() will complete quickly.
            # Tasks already running will complete if they check self.stop_event and exit,
            # or they will run to completion/error if they don't.
            self.executor.shutdown(wait=True)
            self.executor = None # Mark executor as shut down

        self._log("Queue processing stopped.")

    def _task_done_callback(self, future, task_id):
        """Callback executed when a future (task) finishes or is cancelled."""
        with self.queue_lock:
            self.active_tasks.pop(task_id, None)
            task = self.get_task_by_id(task_id) # Get task again to check its final status

        if task: # Check if task still exists (wasn't removed)
            if future.cancelled():
                self._log(f"Task {task_id} ('{task.title}') was cancelled via future.")
                if task.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                     self._update_task_status(task, TaskStatus.CANCELLED, error="Cancelled during shutdown or removal.")
            elif future.exception():
                exc = future.exception()
                self._log(f"Task {task_id} ('{task.title}') failed with exception: {exc}")
                if task.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    self._update_task_status(task, TaskStatus.FAILED, error=str(exc))
            else: # Completed successfully (from _handle_upload perspective)
                 self._log(f"Task {task_id} ('{task.title}') future completed. Final status: {task.status.value}")

        if self.is_processing: # If still processing, try to schedule more tasks
            self._schedule_tasks()


    def _schedule_tasks(self):
        if not self.is_processing or self.stop_event.is_set():
            return

        with self.queue_lock:
            if len(self.active_tasks) >= self.max_concurrent_uploads:
                return # Max concurrent tasks already running

            # Find PENDING tasks to schedule
            tasks_to_schedule = []
            for task in self.queue:
                if task.status == TaskStatus.PENDING and task.task_id not in self.active_tasks:
                    tasks_to_schedule.append(task)
                    if len(self.active_tasks) + len(tasks_to_schedule) >= self.max_concurrent_uploads:
                        break

            for task in tasks_to_schedule:
                if task.task_id not in self.active_tasks: # Double check
                    self._log(f"Submitting task {task.task_id} ('{task.title}') to executor.")
                    future = self.executor.submit(self._handle_upload, task)
                    future.add_done_callback(lambda f, t_id=task.task_id: self._task_done_callback(f, t_id))
                    self.active_tasks[task.task_id] = future
                else:
                    self._log(f"Task {task.task_id} ('{task.title}') was already active, not re-scheduling.")


    def _handle_upload(self, task: VideoUploadTask):
        """Handles the upload of a single video task. Executed by a worker thread."""
        if self.stop_event.is_set(): # Check if manager was stopped before task even started
            self._log(f"Upload for task {task.task_id} ('{task.title}') cancelled before start due to stop_event.")
            self._update_task_status(task, TaskStatus.CANCELLED, error="Cancelled before start.")
            return

        self._update_task_status(task, TaskStatus.INITIALIZING)
        try:
            task.total_size = os.path.getsize(task.file_path)
        except OSError as e:
            self._log(f"Error getting file size for task {task.task_id}: {e}")
            self._update_task_status(task, TaskStatus.FAILED, error=f"File error: {e}")
            return

        init_response = self.peertube_client.upload_video_resumable_init(
            channel_id=task.channel_id,
            file_path=task.file_path,
            video_name=task.title,
            video_description=task.description,
            privacy=task.privacy,
            nsfw=task.nsfw,
            tags=task.tags
        )

        if not init_response or not init_response.get("upload_id"):
            self._update_task_status(task, TaskStatus.FAILED, error="Failed to initialize resumable upload.")
            return

        task.resumable_upload_id = init_response["upload_id"]
        if init_response.get("video_data") and init_response["video_data"].get("id"): # Video might be created at init
             task.video_id_on_peertube = init_response["video_data"]["id"]

        self._update_task_status(task, TaskStatus.UPLOADING, progress=0)

        chunk_size = 1024 * 1024 * 2  # 2MB chunks, configurable
        task.bytes_uploaded = 0

        while task.bytes_uploaded < task.total_size and not self.stop_event.is_set():
            current_chunk_size = min(chunk_size, task.total_size - task.bytes_uploaded)

            upload_status_result = self.peertube_client.upload_video_chunk(
                upload_id=task.resumable_upload_id,
                file_path=task.file_path,
                chunk_start=task.bytes_uploaded,
                chunk_size=current_chunk_size,
                total_size=task.total_size
            )

            if self.stop_event.is_set(): # Check immediately after potentially blocking call
                self._log(f"Stop event set during upload of task {task.task_id}. Attempting to cancel.")
                if task.resumable_upload_id: # Cancel on server
                    self.peertube_client.cancel_resumable_upload(task.resumable_upload_id)
                self._update_task_status(task, TaskStatus.CANCELLED, error="Upload cancelled by user.")
                return


            if upload_status_result.get("status") == "chunk_uploaded":
                task.bytes_uploaded += current_chunk_size
                task.progress = int((task.bytes_uploaded / task.total_size) * 100)
                self._update_task_status(task, TaskStatus.UPLOADING, progress=task.progress)
            elif upload_status_result.get("status") == "complete":
                task.bytes_uploaded = task.total_size # Ensure it's marked as fully uploaded
                task.progress = 100
                video_info = upload_status_result.get("video_info", {})
                video_id = video_info.get("id") or video_info.get("uuid") or task.video_id_on_peertube
                self._update_task_status(task, TaskStatus.COMPLETED, progress=100, video_id=video_id)
                self._log(f"Task '{task.title}' completed successfully. Video ID: {video_id}")
                break
            else: # Error
                error_msg = upload_status_result.get("message", "Chunk upload failed.")
                self._update_task_status(task, TaskStatus.FAILED, error=error_msg)
                # Optionally try to cancel the resumable upload on PeerTube side
                # self.peertube_client.cancel_resumable_upload(task.resumable_upload_id)
                break
        else: # Loop finished
            if self.stop_event.is_set() and task.status != TaskStatus.COMPLETED:
                 self._log(f"Upload for task {task.task_id} was interrupted by stop event.")
                 if task.status not in [TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    self._update_task_status(task, TaskStatus.CANCELLED, error="Upload cancelled by user.")
            elif task.status == TaskStatus.UPLOADING and task.bytes_uploaded == task.total_size:
                # All bytes sent, but didn't get "complete" status from last chunk.
                # This might happen if the server confirms video processing separately.
                # For now, we'll assume if all bytes are sent, it's effectively completed or waiting for server processing.
                # A more robust solution would involve checking the video status on PeerTube.
                self._log(f"All bytes for task '{task.title}' sent. Marking as completed, awaiting server confirmation if any.")
                self._update_task_status(task, TaskStatus.COMPLETED, progress=100, video_id=task.video_id_on_peertube)


    def get_queue_status(self):
        with self.queue_lock:
            return [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "file_path": task.file_path,
                    "status": task.status.value,
                    "progress": task.progress,
                    "error": task.error_message,
                    "video_id": task.video_id_on_peertube
                } for task in self.queue
            ]

    def get_task_by_id(self, task_id):
        with self.queue_lock:
            for task in self.queue:
                if task.task_id == task_id:
                    return task
        return None

    def remove_task(self, task_id):
        with self.queue_lock:
            task_to_remove = self.get_task_by_id(task_id)
            if task_to_remove:
                if task_to_remove.status in [TaskStatus.UPLOADING, TaskStatus.INITIALIZING] and task_to_remove.resumable_upload_id:
                    self._log(f"Attempting to cancel resumable upload {task_to_remove.resumable_upload_id} before removing task {task_id}")
                    self.peertube_client.cancel_resumable_upload(task_to_remove.resumable_upload_id)

                self.queue = [t for t in self.queue if t.task_id != task_id]
                self._log(f"Removed task ID {task_id} from queue.")
                # Notify GUI to remove the item if status_update_callback supports it
                if self.status_update_callback:
                    self.status_update_callback(task_id, TaskStatus.CANCELLED, is_removed=True) # Special flag
                return True
        self._log(f"Task ID {task_id} not found for removal.")
        return False

# Example of how it might be used (for testing, not final integration)
if __name__ == "__main__":
    # Mock PeerTubeClient and callbacks for testing
    class MockPeerTubeClient:
        def __init__(self, instance_url):
            self.instance_url = instance_url
            self.access_token = "dummy_token" # Assume authenticated

        def upload_video_resumable_init(self, channel_id, file_path, video_name, **kwargs):
            print(f"[MockClient] INIT resumable upload for: {video_name} to channel {channel_id}")
            time.sleep(0.5) # Simulate network delay
            return {"upload_id": f"fake-upload-id-{video_name.replace(' ', '')}", "file_size": os.path.getsize(file_path), "video_data": {"id": f"fake-video-id-{time.time()}"}}

        def upload_video_chunk(self, upload_id, file_path, chunk_start, chunk_size, total_size):
            print(f"[MockClient] UPLOADING chunk for {upload_id}: bytes {chunk_start}-{chunk_start + chunk_size -1}/{total_size}")
            time.sleep(0.2) # Simulate network delay
            if chunk_start + chunk_size >= total_size:
                return {"status": "complete", "video_info": {"id": f"final-video-id-for-{upload_id}"}}
            return {"status": "chunk_uploaded"}

        def cancel_resumable_upload(self, upload_id):
            print(f"[MockClient] CANCELLING resumable upload: {upload_id}")
            return True


    def mock_status_update(task_id, status, progress, video_id, error_message, is_new=False, **kwargs):
        if is_new:
            print(f"GUI_NEW_TASK: ID={task_id}, Title='{kwargs.get('title')}', Status={status.value}")
        else:
            print(f"GUI_UPDATE: ID={task_id}, Status={status.value}, Progress={progress}%, VideoID={video_id}, Error='{error_message}'")

    def mock_log_callback(message):
        print(f"GUI_LOG: {message}")

    # Create a dummy file for testing
    dummy_file_1 = "dummy_video1.mp4"
    dummy_file_2 = "dummy_video2.mp4"
    with open(dummy_file_1, "wb") as f:
        f.write(os.urandom(1024 * 1024 * 3)) # 3MB
    with open(dummy_file_2, "wb") as f:
        f.write(os.urandom(1024 * 1024 * 2)) # 2MB


    mock_client = MockPeerTubeClient("http://fake-instance.com")
    queue_mgr = UploadQueueManager(peertube_client=mock_client, status_update_callback=mock_status_update, log_callback=mock_log_callback)

    print("Adding tasks...")
    task1_id = queue_mgr.add_task(file_path=dummy_file_1, channel_id=1, title="My First Test Video")
    task2_id = queue_mgr.add_task(file_path=dummy_file_2, channel_id=2, title="Another Awesome Video", privacy=2)

    print("\nStarting queue processing (will run in background)...")
    # queue_mgr.start_processing() # Already started by add_task if not running

    # Let it run for a bit
    try:
        # Simulate main application running
        main_thread_running_time = 0
        while queue_mgr.is_processing and main_thread_running_time < 20: # Let it run for max 20 seconds
            # Check if all tasks are done
            all_done = True
            with queue_mgr.queue_lock:
                if not queue_mgr.queue: # Queue might be empty if tasks finish very fast
                    all_done = True
                else:
                    for task_obj in queue_mgr.queue:
                        if task_obj.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                            all_done = False
                            break
            if all_done and not queue_mgr.current_task: # Ensure current task also finished
                print("All tasks seem processed.")
                break

            time.sleep(1)
            main_thread_running_time +=1
            if main_thread_running_time == 5 : # Test removing a task mid-way (if it's still there)
                # Find a pending task to remove, or the first one if none are pending
                task_to_remove_id_test = None
                with queue_mgr.queue_lock:
                    for t in queue_mgr.queue:
                        if t.status == TaskStatus.PENDING:
                            task_to_remove_id_test = t.task_id
                            break
                    if not task_to_remove_id_test and queue_mgr.queue: # if no pending, try to remove the first one
                         # task_to_remove_id_test = queue_mgr.queue[0].task_id
                         pass # For this test, let's only try removing PENDING

                if task_to_remove_id_test:
                    print(f"\nAttempting to remove task {task_to_remove_id_test}...")
                    queue_mgr.remove_task(task_to_remove_id_test)


    except KeyboardInterrupt:
        print("\nKeyboard interrupt received.")
    finally:
        print("\nStopping queue processing...")
        queue_mgr.stop_processing()
        print("Queue manager stopped.")

        print("\nFinal Queue Status:")
        for s_task in queue_mgr.get_queue_status():
            print(s_task)

        # Clean up dummy files
        os.remove(dummy_file_1)
        os.remove(dummy_file_2)
        print("Cleaned up dummy files.")
