#!/usr/bin/env python3
# IMPORTANT: IF RUNNING FROM WSL, USE "python.exe" SO YOU RUN THE WINDOWS VERSION OF PYTHON.

import sys
import time
import subprocess
import re

try:
    import pyautogui
except ImportError:
    print("Error: PyAutoGUI is not installed in this Python environment.")
    print("If you are running from WSL, make sure to execute the script using 'python.exe' (Windows Python).")
    print("Otherwise, please install it via: pip install pyautogui")
    sys.exit(1)

def validate_and_parse_ip_port(user_input):
    """
    Validates that user_input matches a valid IP and port format,
    and returns a clean, sanitized 'IP:port' string or None if invalid.
    """
    user_input = user_input.strip()
    match = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})[\s,:]+(\d+)", user_input)
    if not match:
        return None
        
    for group_idx in range(1, 5):
        octet = int(match.group(group_idx))
        if octet < 0 or octet > 255:
            return None
            
    port = int(match.group(5))
    if port < 1 or port > 65535:
        return None
        
    ip = f"{match.group(1)}.{match.group(2)}.{match.group(3)}.{match.group(4)}"
    return f"{ip}:{port}"

def prompt_ip_port():
    while True:
        try:
            user_input = input("Enter Pixel 9 IP address and port (e.g., 192.168.18.57:38237): ").strip()
            if not user_input:
                print("Input cannot be empty. Please try again.")
                continue
            sanitized = validate_and_parse_ip_port(user_input)
            if sanitized:
                return sanitized
            else:
                print("Invalid input. Please enter a valid IP address and port number.")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted by user.")
            sys.exit(1)

def check_pixel_connection(adb_device, quiet=False):
    """Checks if the Pixel device is connected via ADB and responsive."""
    if not quiet:
        print(f"Checking Pixel phone ADB connection ({adb_device})...")
    try:
        # Run adb -s <device> get-state
        result = subprocess.run(
            ["adb", "-s", adb_device, "get-state"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip() == "device":
            if not quiet:
                print(f"Success: Pixel phone is connected and ready ({adb_device}).")
            return True
        else:
            if not quiet:
                stderr_msg = result.stderr.strip() if result.stderr else "Device not found or offline"
                print(f"Error: Pixel phone connection check failed.")
                print(f"ADB output: {result.stdout.strip()}")
                print(f"ADB error: {stderr_msg}")
            return False
    except FileNotFoundError:
        print("Error: 'adb' executable not found in PATH.")
        print("Please verify that Android Platform Tools (adb) is installed and added to your system environment variables.")
        return False
    except Exception as e:
        if not quiet:
            print(f"Error checking ADB connection: {e}")
        return False

def ensure_pixel_connection(adb_device):
    """Verifies connection to the Pixel device. If not connected, attempts to connect using ADB."""
    if check_pixel_connection(adb_device, quiet=True):
        print(f"Success: Pixel phone is already connected and ready ({adb_device}).")
        return True

    print(f"Pixel phone ({adb_device}) is not connected. Attempting to connect automatically...")
    try:
        connect_result = subprocess.run(
            ["adb", "connect", adb_device],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )
        # Check connection again
        if check_pixel_connection(adb_device, quiet=True):
            print(f"Success: Automatically connected to Pixel phone ({adb_device}) and device is ready.")
            return True
        else:
            print(f"Error: Pixel phone connection check failed after attempting to connect.")
            if connect_result.stdout:
                print(f"ADB connect output: {connect_result.stdout.strip()}")
            if connect_result.stderr:
                print(f"ADB connect error: {connect_result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"Error trying to connect to Pixel phone: {e}")
        return False

def press_f9():
    """Presses the F9 hotkey using PyAutoGUI."""
    print("Pressing F9 hotkey...")
    pyautogui.press('f9')

def send_volume_up(adb_device):
    """Sends volume up keyevent to the Pixel camera app via ADB."""
    print("Sending volume up command to Pixel device...")
    try:
        result = subprocess.run(
            ["adb", "-s", adb_device, "shell", "input", "keyevent", "24"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("Volume up command sent successfully.")
            return True
        else:
            print(f"Failed to send volume up command. ADB error: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"Error sending ADB command: {e}")
        return False

def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ["start", "stop"]:
        print("Usage:")
        print("  python record_in_sync.py start  - Starts synchronized recording")
        print("  python record_in_sync.py stop   - Stops synchronized recording")
        sys.exit(1)

    command = sys.argv[1].lower()

    # Ask for IP and Port directly
    adb_device = prompt_ip_port()

    # Check connection first
    if not ensure_pixel_connection(adb_device):
        print(f"\nAborting recording {command} due to connection error.")
        sys.exit(1)

    if command == "start":
        # Print prerequisites
        print("\n=== Prerequisites ===")
        print("1. Wondershare Filmora must be open, the screen recorder must be active,")
        print("   and it must be responsive to the F9 hotkey.")
        print("2. The Pixel camera app must be open on your phone.")
        print("=====================\n")

        # Confirm with user
        confirm = input("Have you fulfilled these prerequisites? (y/n): ").strip().lower()
        if confirm not in ["y", "yes"]:
            print("Aborting recording start.")
            sys.exit(0)

        print("\nStarting synchronized recording...")
        
        # 1. Press F9
        press_f9()

        # 2. Send volume up
        send_volume_up(adb_device)

        print("\nSynchronized recording started!")

    elif command == "stop":
        print("\nStopping synchronized recording...")

        # 1. Press F9
        press_f9()

        # 2. Send volume up
        send_volume_up(adb_device)

        print("\nSynchronized recording stopped!")

if __name__ == "__main__":
    main()
