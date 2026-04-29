import os
import sys
from datetime import datetime
from pathlib import Path

from config.config_handler import ConfigHandler

COLORS = {
    'INFO': '\033[92m',     # Green
    'DEBUG': '\033[94m',    # Blue
    'WARNING': '\033[93m',  # Yellow
    'ERROR': '\033[91m',    # Red
    'CRITICAL': '\033[95m', # Magenta
    'RESET': '\033[0m',
}

# Default log file path
if not os.path.exists("logs"):
    os.makedirs("logs")
LOG_FILE = Path("logs/simulator.log")

def _log(level: str, message: str, to_console: bool = True, to_file: bool = False):
    # Errors and critical messages are always logged regardless of enable_logging setting
    if not(level in ["ERROR", "CRITICAL"] or ConfigHandler().get('simulation', 'enable_logging')):
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color = COLORS.get(level, '')
    reset = COLORS['RESET']

    formatted = f"[{ConfigHandler().get('scheduler', 'type')}] [{timestamp}] [{level}] {message}"

    # Print to console with color
    output = f"{color}{formatted}{reset}"
    if to_console:
        print(output, file=sys.stderr if level in ["ERROR", "CRITICAL"] else sys.stdout)

    # Optionally write to file
    file_logging_enabled = ConfigHandler().get('simulation', 'enable_file_logging')
    if file_logging_enabled and to_file:
        with LOG_FILE.open("a") as f:
            f.write(formatted + "\n")

# Wrapper functions for different log levels
def log_info(msg: str, to_console: bool = True, to_file: bool = True): _log("INFO", msg, to_console, to_file)
def log_debug(msg: str, to_console: bool = True, to_file: bool = True): _log("DEBUG", msg, to_console, to_file)
def log_warning(msg: str, to_console: bool = True, to_file: bool = True): _log("WARNING", msg, to_console, to_file)
def log_error(msg: str, to_console: bool = True, to_file: bool = True): _log("ERROR", msg, to_console, to_file)
def log_critical(msg: str, to_console: bool = True, to_file: bool = True): _log("CRITICAL", msg, to_console, to_file)

# Clear the log file at the start of a new simulation run
def reset():
    if LOG_FILE.exists():
        os.remove(LOG_FILE)