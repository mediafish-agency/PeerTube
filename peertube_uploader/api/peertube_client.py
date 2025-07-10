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

        response = None # Ensure response is defined for all paths in except blocks
        upload_data = None
        try:
            print(f"Initializing resumable upload for {video_name} to channel {channel_id}")
            print(f"Headers: {headers}")
            print(f"Payload: {json.dumps(payload)}")
            response = requests.post(upload_url, headers=headers, json=payload)

            try:
                upload_data = response.json()
            except json.JSONDecodeError as json_err_inner:
                print(f"CRITICAL: JSONDecodeError immediately after request. Status: {response.status_code}. Response Text: '{response.text}'")
                if not (200 <= response.status_code < 300): # If not a success code, raise to outer HTTPError handler
                    response.raise_for_status()
                # If it was 200/201 but empty body, upload_data remains None, handled below.
                print(f"Note: JSONDecodeError means upload_data is None. Error: {json_err_inner}")


            if response.status_code == 201:
                location_header = response.headers.get('Location')
                if not location_header or "?upload_id=" not in location_header:
                    print(f"Error: Status 201 but Location header missing/malformed. Header: '{location_header}'. Text: '{response.text}'")
                    return None
                upload_id = location_header.split("?upload_id=")[-1]
                video_info_from_body = upload_data.get("video") if upload_data is not None else None
                if video_info_from_body:
                    print(f"Resumable upload initialized (201). Upload ID: {upload_id}. Video ID: {video_info_from_body.get('id')}")
                else:
                    print(f"Resumable upload initialized (201 via Location header). Upload ID: {upload_id}. JSON body was empty or invalid, video ID not yet available from body.")
                return {"upload_id": upload_id, "file_size": file_size, "video_data": video_info_from_body}

            elif response.status_code == 200:
                if upload_data is None:
                    print(f"Error: Status 200 but response not valid JSON. Text: '{response.text}'")
                    return None
                location_header = response.headers.get('Location')
                upload_id = None
                if location_header and "?upload_id=" in location_header:
                    upload_id = location_header.split("?upload_id=")[-1]
                if not upload_id and upload_data.get("upload_id"): upload_id = upload_data.get("upload_id")
                elif not upload_id and upload_data.get("video", {}).get("resumableUpload", {}).get("uploadId"):
                    upload_id = upload_data["video"]["resumableUpload"]["uploadId"]

                if not upload_id:
                    print(f"Error: Status 200 but could not get upload_id. Location: '{location_header}'. Body: '{upload_data}'")
                    return None
                print(f"Resumable upload session found/resumed (200). Upload ID: {upload_id}")
                return {"upload_id": upload_id, "file_size": file_size, "video_data": upload_data.get("video")}

            else: # Other non-200/201 codes
                print(f"Upload init failed with status {response.status_code}. Body: '{response.text}'")
                response.raise_for_status() # Raise HTTPError to be caught below

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error in resumable_init: {http_err}")
            if http_err.response is not None and not upload_data : # if upload_data (from response.json()) is None, print raw text
                 print(f"Raw HTTPError response text: {http_err.response.text}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"RequestException in resumable_init: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Underlying response status: {e.response.status_code}. Text: '{e.response.text}'")
            return None
        # Outer json.JSONDecodeError should not be hit if inner one is handled, but as a fallback.
        except json.JSONDecodeError as json_err_outer:
            status = response.status_code if response is not None else "N/A"
            text = response.text if response is not None else "No response"
            print(f"Outer JSONDecodeError in resumable_init. Status: {status}. Text: '{text}'. Error: {json_err_outer}")
            return None
        return None # Should be unreachable if all paths return


    def upload_video_chunk(self, upload_id, file_path, chunk_start, chunk_size, total_size, progress_callback=None):
        """
        Uploads a chunk of the video file for resumable upload.
        API: PUT /api/v1/videos/upload-resumable?upload_id=<upload_id>
        """
        if not self.access_token:
            print("Error: Not authenticated for chunk upload.")
            return {"status": "error", "message": "Not authenticated."}

        upload_url = f"{self.instance_url}/api/v1/videos/upload-resumable?upload_id={upload_id}"

        try:
            with open(file_path, 'rb') as f:
                f.seek(chunk_start)
                data_chunk = f.read(chunk_size)
        except IOError as e:
            print(f"Error reading file chunk: {e}")
            return {"status": "error", "message": str(e)}

        if not data_chunk and chunk_start < total_size: # Check if not at EOF for empty chunk
            print("Warning: Attempting to upload an empty chunk before EOF.")
            # This might be okay for final confirmation or zero-byte files if total_size is 0.
            # If total_size > 0 and chunk_start < total_size, an empty data_chunk is an issue.
            return {"status": "error", "message": "Read empty chunk before EOF"}


        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/offset+octet-stream',
            'Upload-Offset': str(chunk_start),
            'Content-Length': str(len(data_chunk)),
            'Content-Range': f'bytes {chunk_start}-{chunk_start + len(data_chunk) - 1}/{total_size}'
        }

        response = None
        try:
            response = requests.put(upload_url, headers=headers, data=data_chunk)

            if response.status_code == 204: # Chunk successfully received.
                if progress_callback:
                    progress_callback(len(data_chunk))
                return {"status": "chunk_uploaded"}

            elif response.status_code == 200: # Can also mean final chunk processed by some PeerTube versions.
                if progress_callback:
                    progress_callback(len(data_chunk))

                video_info = None
                try:
                    # Some PeerTube versions might return video info in the body of a 200 for the final chunk.
                    video_info = response.json().get("video")
                    print("Final chunk uploaded and video processed (200 OK with JSON body).")
                except json.JSONDecodeError:
                    # Or it might be a 200 OK with an empty body, just confirming completion.
                    print("Final chunk uploaded (200 OK, empty/non-JSON body). Assuming complete.")
                return {"status": "complete", "video_info": video_info}

            elif response.status_code == 308: # Resume Incomplete - Standard TUS response.
                 print(f"Server responded with 308 Resume Incomplete. Headers: {response.headers}")
                 # A robust client would now make a HEAD request to the Location URL from init
                 # to get the server's `Upload-Offset` and resume from there.
                 # For this client, we assume sequential uploads are fine if no error.
                 if progress_callback: # Assume chunk was processed if 308 without error body
                    progress_callback(len(data_chunk))
                 return {"status": "chunk_uploaded", "needs_offset_check": True}
            else:
                # Any other status code indicates an error for this chunk.
                print(f"Chunk upload failed with status: {response.status_code}. Response: '{response.text}'")
                response.raise_for_status() # Raise HTTPError for 4xx/5xx to be caught below.

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error uploading chunk: {http_err}")
            err_resp_text = http_err.response.text if http_err.response is not None else "No response body"
            print(f"Response content: {err_resp_text}")
            return {"status": "error", "message": f"HTTP Error: {str(http_err)} - {err_resp_text}"}
        except requests.exceptions.RequestException as e: # Network errors, DNS, connection refused etc.
            print(f"Network/Request error uploading chunk: {e}")
            return {"status": "error", "message": f"RequestException: {str(e)}"}
        except Exception as e: # Catch any other unexpected error during chunk processing
            print(f"Unexpected error during chunk upload: {e}")
            return {"status": "error", "message": f"Unexpected error: {str(e)}"}

        # Fallback if no other condition was met (should be rare)
        return {"status": "error", "message": "Unknown error after chunk upload attempt."}


    def cancel_resumable_upload(self, upload_id):
        if not self.access_token:
            print("Error: Not authenticated for cancelling upload.")
            return False

        cancel_url = f"{self.instance_url}/api/v1/videos/upload-resumable?upload_id={upload_id}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Length': '0'
        }
        response = None
        try:
            response = requests.delete(cancel_url, headers=headers)
            if response.status_code == 204:
                print(f"Resumable upload {upload_id} cancelled successfully.")
                return True
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error cancelling resumable upload: {http_err}")
            if http_err.response is not None:
                 print(f"Response content: {http_err.response.text}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error cancelling resumable upload: {e}")
            return False
        return False

if __name__ == '__main__':
    INSTANCE_URL = "YOUR_PEERTUBE_INSTANCE_URL"
    USERNAME = "YOUR_USERNAME"
    PASSWORD = "YOUR_PASSWORD"
    OTP = None

    if INSTANCE_URL == "YOUR_PEERTUBE_INSTANCE_URL":
        print("Please configure INSTANCE_URL, USERNAME, and PASSWORD in the script for testing.")
    else:
        client = PeerTubeClient(instance_url=INSTANCE_URL)
        if client.authenticate(USERNAME, PASSWORD, otp_token=OTP):
            print("Authentication successful.")
            channels = client.get_channels()
            if channels:
                print("\nAvailable Channels:")
                for channel in channels:
                    print(f"  ID: {channel['id']}, Name: {channel['displayName']} (Handle: {channel['name']})")
            else:
                print("Failed to fetch channels or no channels available.")
        else:
            print("Authentication failed.")
