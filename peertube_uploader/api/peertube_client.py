import requests
import json
import os

class PeerTubeClient:
    def __init__(self, instance_url):
        self.instance_url = instance_url.rstrip('/')
        self.access_token = None
        self.client_id = None
        self.client_secret = None
        self.token_expires_at = 0
        self.user_role_id = None # Default to unknown/regular user (e.g. 2)
        self.username = None # Store the username of the authenticated user account

    def _get_oauth_client_creds(self):
        try:
            response = requests.get(f"{self.instance_url}/api/v1/oauth-clients/local")
            response.raise_for_status()
            creds = response.json()
            self.client_id = creds.get("client_id")
            self.client_secret = creds.get("client_secret")
            if not self.client_id or not self.client_secret:
                print("Error: Could not retrieve client_id and client_secret.")
                return False
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error getting OAuth client credentials: {e}")
            return False

    def authenticate(self, username, password, otp_token=None):
        if not self._get_oauth_client_creds():
            return False

        token_url = f"{self.instance_url}/api/v1/users/token"
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'password',
            'response_type': 'code',
            'username': username,
            'password': password
        }
        headers = {}
        if otp_token:
            headers['x-peertube-otp'] = otp_token

        response = None
        try:
            response = requests.post(token_url, data=payload, headers=headers)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            if not self.access_token:
                print("Error: Authentication failed, no access token received.")
                return False
            print(f"Successfully authenticated. Access token: {self.access_token[:20]}...")
            # After getting token, fetch user details to get role
            if not self._fetch_user_details(): # Separate method to get user role
                print("Warning: Authentication token obtained, but failed to fetch user details and role.")
                # Decide if this is a hard fail or proceed with unknown role
            return True
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error during authentication: {http_err}")
            if http_err.response is not None:
                print(f"Response body: {http_err.response.text}")
                if http_err.response.status_code == 400:
                    if "invalid_grant" in http_err.response.text:
                        print("Error: Invalid username, password, or OTP.")
                    elif "invalid_request" in http_err.response.text and "OTP" in http_err.response.text:
                        print("Error: OTP token might be required or is incorrect.")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error during authentication (RequestException): {e}")
            return False
        except json.JSONDecodeError as json_err:
            resp_text = response.text if response is not None else "N/A"
            status = response.status_code if response is not None else "N/A"
            print(f"Error decoding JSON from token endpoint. Status: {status}. Text: '{resp_text}'. Error: {json_err}")
            return False

    def _fetch_user_details(self):
        if not self.access_token:
            print("Error: Cannot fetch user details, no access token.")
            return False

        me_url = f"{self.instance_url}/api/v1/users/me"
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = None
        try:
            response = requests.get(me_url, headers=headers)
            response.raise_for_status()
            user_info = response.json()
            self.user_role_id = user_info.get('role', {}).get('id')
            # The 'account' object within /users/me contains the main account details like username
            self.username = user_info.get('account', {}).get('name')
            print(f"Fetched user details: User: {self.username}, Role ID: {self.user_role_id}")
            return True
        except requests.exceptions.HTTPError as http_err:
            err_text = http_err.response.text if http_err.response is not None else "No response body"
            print(f"HTTP error fetching user details (/users/me): {http_err}. Response: {err_text}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error fetching user details (/users/me): {e}")
            return False
        except json.JSONDecodeError as json_err:
            resp_text = response.text if response is not None else "N/A"
            status_code = response.status_code if response is not None else "N/A"
            print(f"Error decoding JSON for user details (/users/me). Status: {status_code}. Text: '{resp_text}'. Error: {json_err}")
            return False

    def get_channels(self):
        if not self.access_token:
            print("Error: Not authenticated. Cannot fetch channels.")
            return None

        # Ensure user details (including role) are fetched if not already
        if self.user_role_id is None and self.username is None:
            if not self._fetch_user_details():
                print("Failed to fetch user details, cannot determine channel list strategy.")
                return None # Or return empty list, or raise error

        headers = {'Authorization': f'Bearer {self.access_token}'}

        # Role IDs: 0 for Admin, 1 for Moderator, 2 for User
        if self.user_role_id == 0 or self.user_role_id == 1:
            print(f"User is Admin/Moderator (Role: {self.user_role_id}). Fetching all video channels.")
            all_channels_list = []
            start = 0
            count = 50
            total_expected = -1

            while True:
                all_channels_url = f"{self.instance_url}/api/v1/video-channels?start={start}&count={count}&sort=-createdAt"
                response_ch = None
                try:
                    response_ch = requests.get(all_channels_url, headers=headers)
                    response_ch.raise_for_status()
                    page_data = response_ch.json()

                    page_channels = page_data.get('data', [])
                    if total_expected == -1:
                        total_expected = page_data.get('total', 0)

                    if not page_channels:
                        break

                    for ch in page_channels:
                        owner_account_info = ch.get('ownerAccount', {})
                        all_channels_list.append({
                            'id': ch.get('id'),
                            'displayName': ch.get('displayName'),
                            'name': ch.get('name'),
                            'ownerAccountName': owner_account_info.get('name', 'N/A')
                        })

                    if total_expected == 0 or len(all_channels_list) >= total_expected or len(page_channels) < count:
                        break
                    start += count

                except requests.exceptions.HTTPError as http_err_ch:
                    err_text_ch = http_err_ch.response.text if http_err_ch.response is not None else "No response body"
                    print(f"HTTP error fetching all channels page: {http_err_ch}. Response: {err_text_ch}")
                    return all_channels_list if all_channels_list else None
                except requests.exceptions.RequestException as e_ch:
                    print(f"Error fetching all channels page: {e_ch}")
                    return all_channels_list if all_channels_list else None
                except json.JSONDecodeError as json_err_ch:
                    resp_text_ch = response_ch.text if response_ch is not None else "N/A"
                    status_code_ch = response_ch.status_code if response_ch is not None else "N/A"
                    print(f"Error decoding JSON for all channels page. Status: {status_code_ch}. Text: '{resp_text_ch}'. Error: {json_err_ch}")
                    return all_channels_list if all_channels_list else None

            if not all_channels_list:
                print("No channels found on the instance (or error fetching for admin/mod).")
            return all_channels_list
        else: # Regular user or role undetermined (defaulting to regular user behavior)
            print(f"User is regular or role undetermined (Role: {self.user_role_id}). Fetching own channels via /users/me again (or cached).")
            # Re-fetch /users/me to get the 'videoChannels' array for this user
            # This might be slightly redundant if _fetch_user_details was just called,
            # but ensures `user_info` is fresh for this specific path.
            # A more optimized way would be to pass user_info from _fetch_user_details if available.
            response_me_user = None
            try {
                response_me_user = requests.get(f"{self.instance_url}/api/v1/users/me", headers=headers)
                response_me_user.raise_for_status()
                user_info_for_channels = response_me_user.json()

                channels_data = user_info_for_channels.get('videoChannels', [])
                formatted_channels = []
                # Use self.username if available, otherwise try to get from this specific /users/me call
                owner_name = self.username if self.username else user_info_for_channels.get('account', {}).get('name', 'self')
                for ch in channels_data:
                    formatted_channels.append({
                        'id': ch.get('id'),
                        'displayName': ch.get('displayName'),
                        'name': ch.get('name'),
                        'ownerAccountName': owner_name
                    })
                if not formatted_channels:
                     print("No channels found for this user (from /users/me).")
                return formatted_channels
            } except requests.exceptions.HTTPError as http_err:
                err_text = http_err.response.text if http_err.response is not None else "No response body"
                print(f"HTTP error fetching user's own channels: {http_err}. Response: {err_text}")
                return None
            except requests.exceptions.RequestException as e:
                print(f"Error fetching user's own channels: {e}")
                return None
            except json.JSONDecodeError as json_err:
                resp_text = response_me_user.text if response_me_user is not None else "N/A"
                status_code = response_me_user.status_code if response_me_user is not None else "N/A"
                print(f"Error decoding JSON for user's own channels. Status: {status_code}. Text: '{resp_text}'. Error: {json_err}")
                return None

    def upload_video_resumable_init(self, channel_id, file_path, video_name, video_description="", privacy=1, nsfw=False, tags=None):
        if not self.access_token:
            print("Error: Not authenticated for resumable upload init.")
            return None
        if not os.path.exists(file_path):
            print(f"Error: File not found at {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        mime_type = "video/mp4"
        if file_path.lower().endswith(".mkv"): mime_type = "video/x-matroska"
        elif file_path.lower().endswith(".avi"): mime_type = "video/x-msvideo"
        elif file_path.lower().endswith(".mov"): mime_type = "video/quicktime"
        elif file_path.lower().endswith(".webm"): mime_type = "video/webm"

        upload_url = f"{self.instance_url}/api/v1/videos/upload-resumable"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-Upload-Content-Length': str(file_size),
            'X-Upload-Content-Type': mime_type,
            'Content-Type': 'application/json'
        }
        payload = {
            "channelId": channel_id, "name": video_name,
            "filename": os.path.basename(file_path), "privacy": privacy, "nsfw": nsfw
        }
        if video_description: payload["description"] = video_description
        if tags: payload["tags"] = tags

        response = None
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
                if not (200 <= response.status_code < 300):
                    response.raise_for_status()
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

            else:
                print(f"Upload init failed with status {response.status_code}. Body: '{response.text}'")
                response.raise_for_status()

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error in resumable_init: {http_err}")
            if http_err.response is not None and not upload_data :
                 print(f"Raw HTTPError response text: {http_err.response.text}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"RequestException in resumable_init: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Underlying response status: {e.response.status_code}. Text: '{e.response.text}'")
            return None
        except json.JSONDecodeError as json_err_outer: # Fallback
            status = response.status_code if response is not None else "N/A"
            text = response.text if response is not None else "No response"
            print(f"Outer JSONDecodeError in resumable_init. Status: {status}. Text: '{text}'. Error: {json_err_outer}")
            return None
        return None


    def upload_video_chunk(self, upload_id, file_path, chunk_start, chunk_size, total_size, progress_callback=None):
        if not self.access_token:
            print("Error: Not authenticated for chunk upload.")
            return {"status": "error", "message": "Not authenticated."}

        upload_url = f"{self.instance_url}/api/v1/videos/upload-resumable?upload_id={upload_id}"

        data_chunk = None
        try:
            with open(file_path, 'rb') as f:
                f.seek(chunk_start)
                data_chunk = f.read(chunk_size)
        except IOError as e:
            print(f"Error reading file chunk: {e}")
            return {"status": "error", "message": str(e)}

        if not data_chunk and chunk_start < total_size:
             print(f"Error: Read empty chunk from {file_path} at offset {chunk_start} but not at EOF.")
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

            if response.status_code == 204:
                if progress_callback:
                    progress_callback(len(data_chunk))
                return {"status": "chunk_uploaded"}

            elif response.status_code == 200:
                if progress_callback:
                    progress_callback(len(data_chunk))
                video_info = None
                try:
                    video_info = response.json().get("video")
                    print("Final chunk uploaded and video processed (200 OK with JSON body).")
                except json.JSONDecodeError:
                    print("Final chunk uploaded (200 OK, empty/non-JSON body). Assuming complete.")
                return {"status": "complete", "video_info": video_info}

            elif response.status_code == 308:
                 print(f"Server responded with 308 Resume Incomplete. Headers: {response.headers}")
                 if progress_callback:
                    progress_callback(len(data_chunk))
                 return {"status": "chunk_uploaded", "needs_offset_check": True}
            else:
                print(f"Chunk upload failed with status: {response.status_code}. Response: '{response.text}'")
                response.raise_for_status()

        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error uploading chunk: {http_err}")
            err_resp_text = http_err.response.text if http_err.response is not None else "No response body"
            print(f"Response content: {err_resp_text}")
            return {"status": "error", "message": f"HTTP Error: {str(http_err)} - {err_resp_text}"}
        except requests.exceptions.RequestException as e:
            print(f"Network/Request error uploading chunk: {e}")
            return {"status": "error", "message": f"RequestException: {str(e)}"}
        except Exception as e:
            print(f"Unexpected error during chunk upload: {e}")
            return {"status": "error", "message": f"Unexpected error: {str(e)}"}

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
