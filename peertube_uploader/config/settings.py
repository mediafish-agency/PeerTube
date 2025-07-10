# Application settings
# For now, this primarily handles the fixed PeerTube instance URL.
# It could be expanded to load from a file if more complex settings are needed.

PEERTUBE_INSTANCE_URL = "https://store.tadreb.live"

def load_settings():
    """
    Returns a dictionary of application settings.
    Currently, it only returns the fixed instance URL.
    """
    print(f"DEBUG: Loading settings. Instance URL: {PEERTUBE_INSTANCE_URL}")
    return {
        'instance_url': PEERTUBE_INSTANCE_URL
    }

def save_settings(settings_dict):
    """
    Placeholder for saving settings. Not used in current configuration
    as the instance URL is fixed in code.
    """
    print(f"DEBUG: save_settings called with {settings_dict} (not implemented for fixed URL)")
    pass

if __name__ == '__main__':
    # Example of how it might be used
    current_settings = load_settings()
    print(f"Loaded settings: {current_settings}")
    # Example: To save settings (if they were dynamic)
    # current_settings['last_user'] = 'test_user'
    # save_settings(current_settings)
