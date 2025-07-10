# Application settings

# WARNING: Storing plaintext passwords in configuration files is a security risk.
# For production or shared environments, consider more secure methods like
# environment variables, system keychain integration, or prompting the user.
PEERTUBE_INSTANCE_URL = "https://store.tadreb.live"
PEERTUBE_USERNAME = "root"
PEERTUBE_PASSWORD = "Ms3343785@"

def load_settings():
    """
    Returns a dictionary of application settings.
    """
    settings = {
        'instance_url': PEERTUBE_INSTANCE_URL,
        'username': PEERTUBE_USERNAME,
        'password': PEERTUBE_PASSWORD
    }
    print(f"DEBUG: Loading settings. Instance URL: {settings['instance_url']}, User: {settings['username']}")
    return settings

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
