# Placeholder for a more advanced logger
# For now, the application uses print() and direct logging to the GUI QTextEdit.
# This could be expanded to use Python's `logging` module,
# log to files, configure log levels, etc.

import logging
import os

LOG_DIR = "logs" # Example log directory
LOG_FILE = os.path.join(LOG_DIR, "peertube_uploader.log")

def setup_logger(name="peertube_uploader_app", level=logging.INFO):
    """
    Basic logger setup.
    """
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except OSError as e:
            print(f"Warning: Could not create log directory {LOG_DIR}: {e}")
            # Fallback to console logging only if dir creation fails
            logging.basicConfig(level=level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            return logging.getLogger(name)


    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Create handlers
    # Console handler
    # c_handler = logging.StreamHandler()
    # c_handler.setLevel(logging.WARNING) # Example: only warnings and above to console

    # File handler
    try:
        f_handler = logging.FileHandler(LOG_FILE)
        f_handler.setLevel(level) # Log everything at the specified level to file

        # Create formatters and add it to handlers
        log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # c_handler.setFormatter(log_format)
        f_handler.setFormatter(log_format)

        # Add handlers to the logger
        # if not logger.handlers: # Avoid adding multiple times if called repeatedly
            # logger.addHandler(c_handler)
        logger.addHandler(f_handler)
    except IOError as e:
        print(f"Warning: Could not set up file logger for {LOG_FILE}: {e}")
        # Basic config if file handler fails
        logging.basicConfig(level=level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        return logging.getLogger(name)


    return logger

# Example: Get a logger instance
# app_logger = setup_logger()
# app_logger.info("This is an info message from the logger utility.")
# app_logger.warning("This is a warning message.")

if __name__ == '__main__':
    # Test the logger setup
    logger = setup_logger(level=logging.DEBUG)
    logger.debug("Logger debug test.")
    logger.info("Logger info test.")
    logger.warning("Logger warning test.")
    logger.error("Logger error test.")
    print(f"Log file should be at: {os.path.abspath(LOG_FILE)}")
