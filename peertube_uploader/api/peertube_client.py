import requests
import json
import os # For potential future token storage/retrieval

# Define a constant for the token file if we decide to store it
# TOKEN_FILE = os.path.expanduser("~/.peertube_uploader_token.json")

class PeerTubeClient:
    def __init__(self, instance_url):
        self.instance_url = instance_url.rstrip('/')
        self.access_token = None
        self.client_id = None
        self.client_secret = None
        self.token_expires_at = 0 # Placeholder for token expiry

    def _get_oauth_client_creds(self):
        """
        Retrieves client_id and client_secret for OAuth.
        These are typically fixed for a given PeerTube instance for client apps.
        """
        try:
            response = requests.get(f"{self.instance_url}/api/v1/oauth-clients/local")
            response.raise_for_status()
            creds = response.json()
            self.client_id = creds.get("client_id")
            self.client_secret = creds.get("client_secret")
            if not self.client_id or not self.client_secret:
                # Log error or raise an exception
                print("Error: Could not retrieve client_id and client_secret.")
                return False
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error getting OAuth client credentials: {e}")
            # Potentially log this to the GUI log area
            return False

    def authenticate(self, username, password, otp_token=None):
        """
        Authenticates with the PeerTube instance to get an access token.
        """
        if not self._get_oauth_client_creds():
            return False

        token_url = f"{self.instance_url}/api/v1/users/token"
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'password',
            'response_type': 'code', # As per some client examples, though 'token' might also work
            'username': username,
            'password': password
        }
        headers = {}
        if otp_token:
            headers['x-peertube-otp'] = otp_token

        try:
            response = requests.post(token_url, data=payload, headers=headers)
            response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)

            token_data = response.json()
            self.access_token = token_data.get('access_token')

            if not self.access_token:
                print("Error: Authentication failed, no access token received.")
                # Potentially update GUI log
                return False

            print(f"Successfully authenticated. Access token: {self.access_token[:20]}...") # Log part of token for verification
            # Store token expiry if available (token_data.get('expires_in')) and handle refresh later
            return True

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error during authentication: {http_err}")
            if response.status_code == 400:
                print(f"Response body: {response.text}") # Often contains useful error details
                # Specific error for incorrect credentials or OTP
                if "invalid_grant" in response.text:
                     print("Error: Invalid username, password, or OTP.")
                elif "invalid_request" in response.text and "OTP" in response.text : # Approximation
                     print("Error: OTP token might be required or is incorrect.")
            # Update GUI log with specific error
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error during authentication: {e}")
            # Update GUI log
            return False
        except json.JSONDecodeError:
            print(f"Error decoding JSON response from token endpoint: {response.text}")
            return False


    def get_channels(self):
        """
        Fetches a list of video channels for the authenticated user or public channels.
        For this application, we need channels the user can upload to.
        This usually means channels owned by the user.
        """
        if not self.access_token:
            print("Error: Not authenticated. Cannot fetch channels.")
            # Update GUI: "Please authenticate first"
            return None

        # Endpoint to get channels of the current user
        # Based on docs: /api/v1/users/me then parse videoChannels
        # Or more directly: /api/v1/video-channels can be filtered by user if user is admin,
        # but for a regular user, /api/v1/users/me and then iterating its videoChannels array is more reliable.
        # Let's try /api/v1/users/me first.

        me_url = f"{self.instance_url}/api/v1/users/me"
        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }

        try:
            response = requests.get(me_url, headers=headers)
            response.raise_for_status()
            user_info = response.json()

            # videoChannels is an array of channel objects associated with the user
            channels = user_info.get('videoChannels', [])

            # We need channel name (for display) and channelId (for upload API)
            # The API returns channel handle as 'name' and display name as 'displayName'
            # The ID is 'id'
            formatted_channels = []
            for ch in channels:
                formatted_channels.append({
                    'id': ch.get('id'),
                    'displayName': ch.get('displayName'),
                    'name': ch.get('name') # This is the handle
                })

            if not formatted_channels:
                 print("No channels found for this user or user has no channels.")
            return formatted_channels

        except requests.exceptions.RequestException as e:
            print(f"Error fetching channels: {e}")
            # Update GUI log
            return None
        except json.JSONDecodeError:
            print(f"Error decoding JSON response from user info endpoint: {response.text}")
            return None

    def upload_video_resumable_init(self, channel_id, file_path, video_name, video_description="", privacy=1, nsfw=False, tags=None):
        """
        Initializes a resumable video upload.
        API: POST /api/v1/videos/upload-resumable
        """
        if not self.access_token:
            print("Error: Not authenticated for resumable upload init.")
            return None

        if not os.path.exists(file_path):
            print(f"Error: File not found at {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        # Basic MIME type detection, can be improved
        mime_type = "video/mp4" # Default, make more robust if needed
        if file_path.lower().endswith(".mkv"):
            mime_type = "video/x-matroska"
        elif file_path.lower().endswith(".avi"):
            mime_type = "video/x-msvideo"
        elif file_path.lower().endswith(".mov"):
            mime_type = "video/quicktime"
        elif file_path.lower().endswith(".webm"):
            mime_type = "video/webm"


        upload_url = f"{self.instance_url}/api/v1/videos/upload-resumable"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-Upload-Content-Length': str(file_size),
            'X-Upload-Content-Type': mime_type,
            'Content-Type': 'application/json' # Body is JSON for init
        }

        payload = {
            "channelId": channel_id,
            "name": video_name,
            "filename": os.path.basename(file_path), # Required for resumable
            "privacy": privacy, # 1: Public, 2: Unlisted, 3: Private, 4: Internal
            "nsfw": nsfw,
            # "waitTranscoding": True, # Optional
            # "generateTranscription": False # Optional
        }
        if video_description: # Only add description if it's not empty
            payload["description"] = video_description
        else:
            # If description is empty, PeerTube might prefer it to be omitted or explicitly null.
            # For now, omitting if empty. If API requires it, this needs to be handled differently (e.g. default value or GUI enforcement)
            # Based on the error, "Invalid value" for empty string suggests omitting or sending null might be better.
            # Let's try omitting first. If server complains, then try sending "description": None (or null in JSON)
            pass

        if tags:
            payload["tags"] = tags

        try:
            print(f"Initializing resumable upload for {video_name} to channel {channel_id}")
            print(f"Headers: {headers}")
            print(f"Payload: {json.dumps(payload)}")

            response = requests.post(upload_url, headers=headers, json=payload)

            upload_data = None
            try:
                # Attempt to parse JSON first, as successful responses (200, 201) should have JSON.
                # Errors (4xx, 5xx) might also have JSON, or might not.
                upload_data = response.json()
            except json.JSONDecodeError as json_err_inner:
                print(f"CRITICAL: JSONDecodeError immediately after request. Status: {response.status_code}. Response Text: '{response.text}'")
                # If it's a 200/201 but not JSON, that's a server problem.
                # If it's another status code, the HTTPError handler below should catch it if raise_for_status() is called,
                # but we log here to ensure we see the non-JSON body of an error.
                if 200 <= response.status_code < 300: # Success range, but not JSON
                     print("Server returned success status but non-JSON body during resumable init.")
                     # This is unexpected for a 200/201 from this endpoint.
                # We will likely hit response.raise_for_status() next if status is an error code.
                # If not, and we expected JSON (like for 200/201), this is an issue.
                # For now, let this proceed to status code checking, or raise if it was a success code.
                if not (200 <= response.status_code < 300):
                    response.raise_for_status() # Trigger HTTPError if it's an error status
                # If it was 200/201 but not JSON, this is a problem.
                print(f"JSONDecodeError after request (before status check): {json_err_inner}") # Log it
                # Let it fall through to status code check or subsequent error handling
                # If upload_data is still None, it will be handled.


            # Check status codes AFTER attempting to parse JSON (if it was expected or an error occurred)
            if response.status_code == 201: # Created - Primary success case for new upload
                location_header = response.headers.get('Location')
                if not location_header or "?upload_id=" not in location_header:
                    print(f"Error: Status 201 but Location header missing or malformed for upload_id. Header: '{location_header}'. Response Text: '{response.text}'")
                    return None

                upload_id = location_header.split("?upload_id=")[-1]
                video_info_from_body = upload_data.get("video") if upload_data is not None else None

                if video_info_from_body:
                    print(f"Resumable upload initialized (201). Upload ID: {upload_id}. Video created with ID: {video_info_from_body.get('id')}")
                else: # This case will be hit if body was empty but Location header was fine
                    print(f"Resumable upload initialized (201 via Location header). Upload ID: {upload_id}. JSON body was empty or invalid, video ID not yet available from body.")
                return {"upload_id": upload_id, "file_size": file_size, "video_data": video_info_from_body}

            elif response.status_code == 200: # OK - Usually for resuming an existing upload, MUST have JSON.
                if upload_data is None:
                    print(f"Error: Server returned status 200 but response was not valid JSON (or empty). Response text: '{response.text}'")
                    return None

                location_header = response.headers.get('Location')
                upload_id = None
                if location_header and "?upload_id=" in location_header:
                    upload_id = location_header.split("?upload_id=")[-1]

                # For 200 on POST init, TUS implies client might have tried to create an upload that already exists.
                # The server might return details of the existing upload.
                # PeerTube specific: if upload_data contains 'upload_id' or 'video.id', it's useful.
                # We need a reliable way to get the upload_id to proceed. Location is preferred.
                if not upload_id and upload_data.get("upload_id"): # Fallback to body if present
                    upload_id = upload_data.get("upload_id")
                elif not upload_id and upload_data.get("video", {}).get("resumableUpload", {}).get("uploadId"): # More nested possibility
                    upload_id = upload_data["video"]["resumableUpload"]["uploadId"]

                if not upload_id:
                    print(f"Error: Status 200 but could not determine upload_id. Location: '{location_header}'. Body: '{upload_data}'")
                    return None

                print(f"Resumable upload session found/resumed (200). Upload ID: {upload_id}")
                video_info_from_body = upload_data.get("video")
                return {"upload_id": upload_id, "file_size": file_size, "video_data": video_info_from_body}

            else: # Not 200 or 201
                # If we got here, it means response.json() might have succeeded on an error code (e.g. 400 with JSON body)
                # or raise_for_status() was not hit in the JSONDecodeError block.
                # We should ensure an error is raised if not already.
                if not (200 <= response.status_code < 300): # If it's an error status code
                    if upload_data: # We have a JSON body for the error
                        print(f"Error response from server (Status {response.status_code}): {json.dumps(upload_data)}")
                    else: # No JSON body for the error, just text
                        print(f"Error response from server (Status {response.status_code}): {response.text}")
                response.raise_for_status() # This will raise HTTPError for 4xx/5xx

        except requests.exceptions.HTTPError as http_err:
            # This catches raise_for_status() calls
            print(f"HTTP error initializing resumable upload: {http_err}")
            # The response content might have already been logged if upload_data was populated from JSON error body
            if http_err.response and not upload_data: # only print if not already printed via upload_data
                print(f"Response content for HTTPError: {http_err.response.text}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error initializing resumable upload (RequestException): {e}")
            if e.response is not None:
                print(f"Underlying response status code: {e.response.status_code}")
                print(f"Underlying response text: '{e.response.text}'")
            return None
        except json.JSONDecodeError as json_err:
            # This specific block for JSONDecodeError on the main response.json() call
            # might be less likely to be hit directly if the nested one catches it first,
            # but kept for robustness.
            print(f"Outer JSONDecodeError during resumable init. Status: {response.status_code if response else 'N/A'}")
            raw_response_text = response.text if response else "No response object"
            print(f"Raw response text for outer JSONDecodeError: '{raw_response_text}'")
            print(f"Outer JSONDecodeError details: {json_err}")
            return None

    def upload_video_chunk(self, upload_id, file_path, chunk_start, chunk_size, total_size, progress_callback=None):
                # Based on tus protocol, Location header is key for subsequent PUTs.
                # Let's check response headers and body.
                # PeerTube's resumable upload API might differ slightly from pure TUS.
                # The docs say: `queryParameters.upload_id` (string, required) for the PUT chunk
                # and `headerParameters.Location` is not explicitly mentioned for POST init response.
                # The response to POST /api/v1/videos/upload-resumable (201 or 200)
                # gives a Location header like: https://instance.com/api/v1/videos/upload-resumable?upload_id=xxxx

                location_header = response.headers.get('Location')
                if not location_header:
                    # Fallback if Location header is not there, check body for upload_id or video id
                    # This part needs to align with how PeerTube actually returns the upload_id or session.
                    # The docs for PUT /api/v1/videos/upload-resumable say:
                    # queryParameters.upload_id (required) string: Created session id to proceed with.
                    # It's likely this upload_id is returned in the Location header of the POST init.
                     print(f"Warning: No Location header in resumable init response. Body: {response.text}")
                     # Attempt to parse upload_id from location_header if it's a full URL
                     if location_header and "?upload_id=" in location_header:
                         upload_id = location_header.split("?upload_id=")[-1]
                         return {"upload_id": upload_id, "file_size": file_size, "video_data": upload_data.get("video")} # video_data may contain the created video ID/UUID
                     else: # Try to get it from body if it's there (less standard for TUS but possible)
                         # This part is speculative based on general API patterns, not specific PeerTube TUS docs
                         # For now, we'll rely on Location header as per TUS.
                         print("Error: Could not determine upload_id for resumable upload.")
                         return None


                # Extract upload_id from Location header
                # e.g., Location: /api/v1/videos/upload-resumable?upload_id=xxxxxxxxxxxx
                if "?upload_id=" in location_header:
                    upload_id = location_header.split("?upload_id=")[-1]
                    print(f"Resumable upload initialized. Upload ID: {upload_id}")
                    return {"upload_id": upload_id, "file_size": file_size, "video_data": upload_data.get("video")}
                else:
                    print(f"Error: Could not parse upload_id from Location header: {location_header}")
                    return None

            else:
                response.raise_for_status() # This will raise for other errors

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error initializing resumable upload: {http_err}")
            print(f"Response content: {http_err.response.text}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error initializing resumable upload (RequestException): {e}")
            if e.response is not None:
                print(f"Underlying response status code: {e.response.status_code}")
                print(f"Underlying response text: '{e.response.text}'")
            return None
        except json.JSONDecodeError as json_err:
            # This block might be preempted if RequestException catches JSONDecodeError from response.json()
            print(f"Error decoding JSON response from resumable init. Status Code: {response.status_code if response else 'N/A'}")
            raw_response_text = response.text if response else "No response object"
            print(f"Raw response text for JSONDecodeError: '{raw_response_text}'")
            print(f"JSONDecodeError: {json_err}")
            return None
        # Removed the final 'return None' here as all paths should return within try/except

    def upload_video_chunk(self, upload_id, file_path, chunk_start, chunk_size, total_size, progress_callback=None):
        """
        Uploads a chunk of the video file for resumable upload.
        API: PUT /api/v1/videos/upload-resumable?upload_id=<upload_id>
        """
        if not self.access_token:
            print("Error: Not authenticated for chunk upload.")
            return False # Indicate failure

        upload_url = f"{self.instance_url}/api/v1/videos/upload-resumable?upload_id={upload_id}"

        try:
            with open(file_path, 'rb') as f:
                f.seek(chunk_start)
                data_chunk = f.read(chunk_size)
        except IOError as e:
            print(f"Error reading file chunk: {e}")
            return False

        if not data_chunk: # Should not happen if chunk_start and chunk_size are correct unless it's the very end
            print("Warning: Attempting to upload an empty chunk.")
            # This might be okay if it's a zero-byte file or final confirmation.
            # TUS protocol usually expects a final empty PUT to complete, but PeerTube might differ.
            # For now, let's assume non-empty chunks unless it's the very final one.

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/offset+octet-stream', # As per TUS / resumable upload standard
            'Upload-Offset': str(chunk_start), # Standard TUS header
            'Content-Length': str(len(data_chunk)), # Actual size of the chunk being sent
            # PeerTube specific resumable upload (from docs) uses Content-Range
            'Content-Range': f'bytes {chunk_start}-{chunk_start + len(data_chunk) - 1}/{total_size}'
        }

        # print(f"Uploading chunk for {upload_id}: bytes {chunk_start}-{chunk_start + len(data_chunk) - 1}/{total_size}")

        try:
            response = requests.put(upload_url, headers=headers, data=data_chunk)

            if response.status_code == 204 or response.status_code == 200: # 204 No Content (chunk accepted), 200 OK (final chunk, video processed)
                if progress_callback:
                    progress_callback(len(data_chunk)) # Report bytes uploaded in this chunk

                if response.status_code == 200: # Final chunk processed and video created/updated
                    print("Final chunk uploaded and video processed.")
                    video_info = response.json().get("video")
                    return {"status": "complete", "video_info": video_info}
                return {"status": "chunk_uploaded"} # Chunk successfully uploaded

            elif response.status_code == 308: # Resume Incomplete (TUS standard, check if PeerTube uses it)
                 print(f"Server responded with 308 Resume Incomplete. Headers: {response.headers}")
                 # Client should then query server for current offset using HEAD request
                 # For now, we'll assume PeerTube doesn't strictly require this for simple sequential uploads
                 return {"status": "chunk_uploaded"} # Treat as success for now, but might need offset check

            else:
                response.raise_for_status() # Will raise for other 4xx/5xx errors

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error uploading chunk: {http_err}")
            print(f"Response content: {http_err.response.text}")
            return {"status": "error", "message": str(http_err)}
        except requests.exceptions.RequestException as e:
            print(f"Error uploading chunk: {e}")
            return {"status": "error", "message": str(e)}

        return {"status": "error", "message": "Unknown error during chunk upload."}

    def cancel_resumable_upload(self, upload_id):
        """
        Cancels an ongoing resumable upload.
        API: DELETE /api/v1/videos/upload-resumable?upload_id=<upload_id>
        """
        if not self.access_token:
            print("Error: Not authenticated for cancelling upload.")
            return False

        cancel_url = f"{self.instance_url}/api/v1/videos/upload-resumable?upload_id={upload_id}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Length': '0' # Required by PeerTube docs for this endpoint
        }

        try:
            response = requests.delete(cancel_url, headers=headers)
            if response.status_code == 204:
                print(f"Resumable upload {upload_id} cancelled successfully.")
                return True
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error cancelling resumable upload: {http_err}")
            if http_err.response:
                 print(f"Response content: {http_err.response.text}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error cancelling resumable upload: {e}")
            return False
        return False

# Example Usage (for testing purposes, normally called from GUI/QueueManager)
if __name__ == '__main__':
    # Replace with your PeerTube instance URL, username, and password
    INSTANCE_URL = "YOUR_PEERTUBE_INSTANCE_URL"
    USERNAME = "YOUR_USERNAME"
    PASSWORD = "YOUR_PASSWORD"
    OTP = None # Your OTP if 2FA is enabled, otherwise None

    if INSTANCE_URL == "YOUR_PEERTUBE_INSTANCE_URL":
        print("Please configure INSTANCE_URL, USERNAME, and PASSWORD in the script for testing.")
    else:
        client = PeerTubeClient(instance_url=INSTANCE_URL)

        # Test authentication
        if client.authenticate(USERNAME, PASSWORD, otp_token=OTP):
            print("Authentication successful.")

            # Test fetching channels
            channels = client.get_channels()
            if channels:
                print("\nAvailable Channels:")
                for channel in channels:
                    print(f"  ID: {channel['id']}, Name: {channel['displayName']} (Handle: {channel['name']})")

                # Example: Test Resumable Upload (replace with actual file and details)
                # Ensure test_video.mp4 exists in the same directory or provide full path
                # TEST_VIDEO_PATH = "test_video.mp4"
                # if os.path.exists(TEST_VIDEO_PATH) and channels:
                #     first_channel_id = channels[0]['id']
                #     print(f"\nAttempting to upload '{TEST_VIDEO_PATH}' to channel ID {first_channel_id}...")

                #     init_response = client.upload_video_resumable_init(
                #         channel_id=first_channel_id,
                #         file_path=TEST_VIDEO_PATH,
                #         video_name="Test Resumable Upload API",
                #         video_description="This is a test video uploaded via API.",
                #         privacy=3 # Private
                #     )

                #     if init_response and init_response.get("upload_id"):
                #         upload_id = init_response["upload_id"]
                #         total_size = init_response["file_size"]
                #         chunk_size = 1024 * 1024 * 1 # 1MB chunks
                #         bytes_uploaded = 0

                #         while bytes_uploaded < total_size:
                #             current_chunk_size = min(chunk_size, total_size - bytes_uploaded)
                #             print(f"Uploading chunk: offset {bytes_uploaded}, size {current_chunk_size}")

                #             upload_status = client.upload_video_chunk(
                #                 upload_id, TEST_VIDEO_PATH, bytes_uploaded, current_chunk_size, total_size
                #             )

                #             if upload_status.get("status") == "chunk_uploaded":
                #                 bytes_uploaded += current_chunk_size
                #                 progress = (bytes_uploaded / total_size) * 100
                #                 print(f"Progress: {progress:.2f}%")
                #             elif upload_status.get("status") == "complete":
                #                 print(f"Upload complete! Video Info: {upload_status.get('video_info')}")
                #                 break
                #             else:
                #                 print(f"Chunk upload failed: {upload_status.get('message')}")
                #                 # client.cancel_resumable_upload(upload_id) # Optional: cleanup
                #                 break
                #         else: # Executed if the loop completes without break (i.e. all chunks sent, last one was not 'complete' status)
                #             if bytes_uploaded == total_size:
                #                  print("All chunks sent, but final 'complete' status not received directly from chunk upload. Might require a final confirmation or check.")
                #     else:
                #         print("Failed to initialize resumable upload.")
                # else:
                #     if not os.path.exists(TEST_VIDEO_PATH):
                #         print(f"\nTest video '{TEST_VIDEO_PATH}' not found. Skipping upload test.")

            else:
                print("Failed to fetch channels or no channels available.")
        else:
            print("Authentication failed.")
