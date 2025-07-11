import time
import threading
from enum import Enum
import os

from api.peertube_client import PeerTubeClient


class TaskStatus(Enum):
    PENDING = "Pending"
    UPLOADING = "Uploading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    INITIALIZING = "Initializing" # For resumable upload init phase

class VideoUploadTask:
    # Default privacy to 2 (Unlisted) to match UI default.
    def __init__(self, task_id, file_path, channel_id, title, description="", privacy=2, nsfw=False, tags=None):
        self.task_id = task_id
        self.file_path = file_path
        self.channel_id = channel_id
        self.title = title
        self.description = description
        self.privacy = privacy # Store the passed privacy value
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
    def __init__(self, peertube_client: PeerTubeClient, status_update_callback=None, log_callback=None, upload_chunk_size_mb=4):
        self.queue = []
        self.peertube_client = peertube_client
        self.current_task = None
        self.is_processing = False
        self.is_manually_paused = False # New attribute for pause/resume state
        self.stop_event = threading.Event()
        self.queue_lock = threading.Lock()
        self.task_id_counter = 0
        self.processing_thread = None
        self.upload_chunk_size_mb = upload_chunk_size_mb # Use passed argument, default to 4MB
        self.chunk_size_bytes = self.upload_chunk_size_mb * 1024 * 1024


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

    # Update signature to include privacy, default to 2 (Unlisted)
    def add_task(self, file_path, channel_id, title, description="", privacy=2, nsfw=False, tags=None):
        with self.queue_lock:
            self.task_id_counter += 1
            # Pass the received privacy to VideoUploadTask constructor
            task = VideoUploadTask(self.task_id_counter, file_path, channel_id, title, description, privacy, nsfw, tags)
            self.queue.append(task)
            self._log(f"Added task: {title} (ID: {task.task_id}, Privacy: {privacy}) to queue. File: {file_path}")
            if self.status_update_callback: # Notify GUI about new task in queue
                 self.status_update_callback(task.task_id, task.status, task.progress, None, None, is_new=True, file_path=task.file_path, title=task.title, channel_id=task.channel_id, is_removed=False)

        if not self.is_processing:
            self.start_processing()
        # If it is processing but was paused, adding a task might implicitly resume
        # or user has to explicitly resume. For now, let's assume explicit resume.
        return task.task_id

    def start_processing(self):
        if self.is_processing and self.processing_thread and self.processing_thread.is_alive():
            self._log("Processing already started or thread active.")
            if self.is_manually_paused:
                 self._log("Queue is paused. Call resume_processing() to start uploads.")
            return

        if not self.peertube_client or not self.peertube_client.access_token:
            self._log("Cannot start processing: PeerTube client not authenticated.")
            return

        self.is_processing = True # Overall manager is active
        self.is_manually_paused = False # Explicitly not paused on fresh start
        self.stop_event.clear()

        # Ensure only one processing thread is started
        if not self.processing_thread or not self.processing_thread.is_alive():
            self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
            self.processing_thread.start()
            self._log("Started queue processing thread.")
        else:
            self._log("Processing thread already exists and is alive.")


    def pause_processing(self):
        """Pauses the scheduling of new tasks. Currently active task will continue its current operation."""
        if not self.is_processing:
            self._log("Cannot pause, processing is not active.")
            return
        if self.is_manually_paused:
            self._log("Processing is already paused.")
            return

        self.is_manually_paused = True
        self._log("Queue processing paused. Active task will continue its current chunk/operation.")
        # Note: This simple pause doesn't actively interrupt the current task's _handle_upload.
        # _handle_upload only checks stop_event for hard stops.
        # A more aggressive pause might set stop_event and then clear it on resume,
        # but that could cancel the current task's resumable upload.

    def resume_processing(self):
        """Resumes the scheduling of tasks if the queue was manually paused."""
        if not self.is_processing:
            self._log("Cannot resume, processing is not set to active overall.")
            # Potentially call start_processing() here if that's desired behavior for resuming a non-active queue
            # self.start_processing()
            return

        if not self.is_manually_paused:
            self._log("Processing is not paused.")
            return

        self.is_manually_paused = False
        self._log("Queue processing resumed.")
        # If the processing thread is alive, it will pick up tasks in its next loop iteration.
        # If the thread died for some reason and self.is_processing is true, start_processing might be needed.
        # For simplicity, we assume _process_queue loop continues if self.is_processing is true.
        # We might need to signal the _process_queue's wait condition if it's sleeping.
        # However, the current _process_queue loop with time.sleep(2) will naturally pick up.
        # If start_processing wasn't called or thread died, ensure it's running:
        if not self.processing_thread or not self.processing_thread.is_alive():
            self._log("Processing thread was not alive. Restarting it for resume.")
            self.start_processing() # This will re-initialize the thread if needed.


    def stop_processing(self): # This is a "soft" stop, mainly for app shutdown
        self._log("Stopping queue processing (soft stop)...")
        self.is_processing = False # Prevent _process_queue loop from continuing after current task
        self.is_manually_paused = False # Clear pause state
        self.stop_event.set() # Signal current _handle_upload to stop (and cancel resumable)

        # Wait for the processing thread to finish its current task and exit its loop
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=15) # Increased timeout slightly

        if self.processing_thread and self.processing_thread.is_alive():
            self._log("Warning: Processing thread did not terminate cleanly after stop signal.")

        self.processing_thread = None # Clear the thread reference
        self._log("Queue processing stopped.")


    def stop_all_and_clear_tasks(self):
        self._log("Stopping all tasks and clearing queue...")
        self.is_manually_paused = True # Prevent any new scheduling attempts during this operation

        # Signal the current task (if any) to stop and cancel its resumable upload
        self.stop_event.set()

        # If a processing thread is active, tell it to stop fully and wait for it
        # This ensures the current_task's _handle_upload gets the stop_event signal
        if self.processing_thread and self.processing_thread.is_alive():
            self.is_processing = False # Stop the main loop of _process_queue
            self.processing_thread.join(timeout=15)
            if self.processing_thread and self.processing_thread.is_alive():
                 self._log("Warning: Processing thread did not stop cleanly during stop_all_and_clear_tasks.")
            self.processing_thread = None

        # Now that the processing loop is stopped (or was never running), clear the queue
        tasks_to_notify_gui_about = []
        with self.queue_lock:
            tasks_to_notify_gui_about = list(self.queue) # Get a copy
            self.queue.clear()
            if self.current_task: # If there was a task being processed when stop_event was set
                # It might not be in self.queue anymore if _process_queue removed it before stopping.
                # Ensure its resumable upload is cancelled if it has one and wasn't completed/failed.
                if self.current_task.resumable_upload_id and \
                   self.current_task.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    self._log(f"Ensuring cancellation of active task {self.current_task.task_id} on PeerTube.")
                    self.peertube_client.cancel_resumable_upload(self.current_task.resumable_upload_id)
                # Add it to notification list if not already (e.g. if it was the current_task)
                # This is tricky because its status might have been set to CANCELLED by _handle_upload
                # For simplicity, we'll rely on the GUI being updated for tasks in `tasks_to_notify_gui_about`
                # and the current_task's update from _handle_upload.
                self.current_task = None

        # Notify GUI to remove all tasks that were in the queue
        if self.status_update_callback:
            for task_obj in tasks_to_notify_gui_about:
                # Update status to CANCELLED before notifying for removal, if not already failed/completed
                if task_obj.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    task_obj.status = TaskStatus.CANCELLED
                    task_obj.error_message = "Queue cleared by user."

                self.status_update_callback(
                    task_obj.task_id, task_obj.status, task_obj.progress,
                    task_obj.video_id_on_peertube, task_obj.error_message,
                    False, task_obj.file_path, task_obj.title, task_obj.channel_id,
                    True # is_removed = True
                )

        self.is_processing = False # Ensure manager is fully stopped
        self.is_manually_paused = False # Reset pause state
        self.stop_event.clear() # Clear for future operations
        self._log("All tasks stopped and queue cleared.")
        # The queue manager is now idle. It will restart if add_task is called and is_processing is false.

    def reorder_queue(self, new_task_id_order: list[int]):
        """
        Reorders the internal queue based on a new list of task IDs.
        Only PENDING tasks are effectively reordered in terms of processing sequence.
        """
        if not new_task_id_order and not self.queue: # Both empty, nothing to do
            return

        with self.queue_lock:
            self._log(f"Attempting to reorder queue. Current order: {[t.task_id for t in self.queue]}. New visual order: {new_task_id_order}")

            current_tasks_map = {task.task_id: task for task in self.queue}
            new_ordered_queue = []

            processed_ids_in_new_order = set()

            # First, add tasks based on the new_task_id_order
            for task_id in new_task_id_order:
                if task_id in current_tasks_map:
                    new_ordered_queue.append(current_tasks_map[task_id])
                    processed_ids_in_new_order.add(task_id)
                else:
                    self._log(f"Warning: Task ID {task_id} from new order not found in current queue.")

            # Add any tasks that were in the old queue but somehow not in new_task_id_order
            # (e.g. if GUI list was out of sync, or to preserve tasks not visible/mapped)
            # This usually shouldn't happen if new_task_id_order comes from a complete GUI list.
            # These tasks are typically appended to maintain their existence, their relative order preserved.
            for task in self.queue:
                if task.task_id not in processed_ids_in_new_order:
                    self._log(f"Warning: Task ID {task.task_id} from old queue not in new order. Appending it.")
                    new_ordered_queue.append(task)

            self.queue = new_ordered_queue
            self._log(f"Queue reordered. New internal order: {[t.task_id for t in self.queue]}")

        # After reordering, if the queue is not paused and processing is active,
        # the _process_queue loop will naturally pick tasks based on the new order
        # when it looks for the next PENDING task.
        # No explicit call to _process_queue or _schedule_tasks is needed here as the
        # existing _process_queue loop will handle it. If it was paused, it will respect that.


    def _process_queue(self):
        while self.is_processing and not self.stop_event.is_set():
            if self.is_manually_paused:
                time.sleep(1) # Sleep while paused
                continue

            task_to_process = None
            with self.queue_lock:
                # Find first PENDING task
                for task_in_q in self.queue:
                    if task_in_q.status == TaskStatus.PENDING:
                        task_to_process = task_in_q
                        break

            if task_to_process:
                self.current_task = task_to_process # Set current task
                # No need to remove from queue here, _handle_upload doesn't modify queue
                self._log(f"Processing task: {self.current_task.title} (ID: {self.current_task.task_id})")
                self._handle_upload(self.current_task)

                # After _handle_upload finishes (completes, fails, or cancels itself due to stop_event):
                with self.queue_lock:
                    if self.current_task and self.current_task.task_id == task_to_process.task_id:
                        # If the task wasn't removed by another operation (e.g. explicit remove_task call)
                        # and its status indicates it should be removed from pending (e.g. not pending anymore)
                        # This part is tricky because PENDING tasks are not removed from self.queue until processed.
                        # The current logic is that _handle_upload sets the final status.
                        # The queue only contains PENDING tasks that are waiting, or tasks that are being processed.
                        # Let's simplify: _process_queue picks a PENDING task. _handle_upload changes its status.
                        # The task remains in self.queue until explicitly removed by user (remove_task, clear_completed)
                        # or by stop_all_and_clear_tasks.
                        pass # Task remains in queue with its new status.
                    self.current_task = None # Clear current task reference
            else: # No PENDING tasks found
                if self.is_processing: # Still active but no work to do right now
                    # self._log("No pending tasks to process. Waiting...") # Too noisy
                    pass
                time.sleep(1) # Wait before checking queue again

        self._log("Exited processing loop.")
        self.is_processing = False # Ensure state is correct on exit
        self.current_task = None # Clear current task if loop exits


    def _handle_upload(self, task: VideoUploadTask):
        if self.stop_event.is_set(): # Check if stop was requested before even starting this task
            self._update_task_status(task, TaskStatus.CANCELLED, error="Cancelled before start.")
            return

        self._update_task_status(task, TaskStatus.INITIALIZING)
        task.total_size = os.path.getsize(task.file_path)

        init_response = self.peertube_client.upload_video_resumable_init(
            channel_id=task.channel_id,
            file_path=task.file_path,
            video_name=task.title,
            video_description=task.description, # Already defaults to "" in VideoUploadTask
            privacy=task.privacy,               # This is the key line, ensuring task.privacy is passed
            nsfw=task.nsfw,                     # Already defaults to False in VideoUploadTask
            tags=task.tags                      # Already defaults to [] in VideoUploadTask
        )

        if not init_response or not init_response.get("upload_id"):
            self._update_task_status(task, TaskStatus.FAILED, error="Failed to initialize resumable upload.")
            return

        task.resumable_upload_id = init_response["upload_id"]
        if init_response.get("video_data") and init_response["video_data"].get("id"): # Video might be created at init
             task.video_id_on_peertube = init_response["video_data"]["id"]

        self._update_task_status(task, TaskStatus.UPLOADING, progress=0)

        # Use the configurable chunk_size_bytes
        # chunk_size = 1024 * 1024 * 2  # Old hardcoded value
        task.bytes_uploaded = 0

        while task.bytes_uploaded < task.total_size: # Condition simplified, checks moved inside
            if self.stop_event.is_set(): # Priority 1: Hard stop/cancel
                self._log(f"Stop event set during upload of task {task.task_id}. Attempting to cancel.")
                if task.resumable_upload_id: # Cancel on server
                    self.peertube_client.cancel_resumable_upload(task.resumable_upload_id)
                self._update_task_status(task, TaskStatus.CANCELLED, error="Upload cancelled by user.")
                return # Exit _handle_upload

            if self.is_manually_paused: # Priority 2: Soft pause
                time.sleep(0.5)  # Wait a bit before checking again
                continue # Re-evaluate stop_event and is_manually_paused

            # If neither stopped nor paused, proceed with chunk upload
            current_chunk_size = min(self.chunk_size_bytes, task.total_size - task.bytes_uploaded)

            upload_status_result = self.peertube_client.upload_video_chunk(
                upload_id=task.resumable_upload_id,
                file_path=task.file_path,
                chunk_start=task.bytes_uploaded,
                chunk_size=current_chunk_size,
                total_size=task.total_size
            )

            # The stop_event check is now at the top of the loop, so this redundant check is removed.
            # if self.stop_event.is_set(): # Check immediately after potentially blocking call
            #     self._log(f"Stop event set during upload of task {task.task_id}. Attempting to cancel.")
            #     if task.resumable_upload_id: # Cancel on server
            #         self.peertube_client.cancel_resumable_upload(task.resumable_upload_id)
            #     self._update_task_status(task, TaskStatus.CANCELLED, error="Upload cancelled by user.")
            #     return

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
