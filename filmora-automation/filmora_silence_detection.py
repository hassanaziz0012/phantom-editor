# IMPORTANT: THIS SCRIPT MUST BE RUN FROM WINDOWS, NOT WSL!

import os
import subprocess
import pyautogui
import time
from pathlib import Path
import sys

# --- Configuration ---
if len(sys.argv) < 2:
    print("Usage: python filmora_silence_detection.py <path_to_mp4_file>")
    sys.exit(1)

FILE_TO_IMPORT = sys.argv[1]
EXE_PATH = r"C:\Users\hassa\AppData\Local\Wondershare\Wondershare Filmora\Wondershare Filmora Launcher.exe"

# 1. Path Extraction
video_path = Path(FILE_TO_IMPORT)
PROJECT_NAME = video_path.parent.name
SAVE_FULL_PATH = str(video_path.parent / PROJECT_NAME) 

def notify(title, msg):
    print("Notification: ", title, msg)

def find_and_click(image_path, confidence=0.9, retries=3, delay=5, click=True):
    for attempt in range(retries):
        try:
            location = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if location:
                if click: pyautogui.click(location)
                return True
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"UI Element not found: {image_path}")

def run_automation():
    # 1. Launch
    print(f"Log: Launching Filmora for: {PROJECT_NAME}")
    subprocess.Popen(EXE_PATH)
    time.sleep(12) 
    find_and_click('imgs/new-project-btn.png')

    # 2. Editor UI
    print("Log: Waiting for Editor...")
    find_and_click('imgs/filmora-editor-blank-window.png', confidence=0.7, retries=10)

    # 3. Import
    print("Log: Importing media...")
    pyautogui.hotkey('ctrl', 'i')
    time.sleep(2) 
    pyautogui.write(FILE_TO_IMPORT)
    pyautogui.press('enter')
    time.sleep(3) 

    # 4. Timeline
    print("Log: Adding to timeline...")
    pyautogui.click(425, 250) 
    time.sleep(0.5)
    pyautogui.hotkey('shift', 'i')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(2) 

    # 5. Silence Detection
    print("Log: Running Silence Detection...")
    
    location = None
    for attempt in range(5):
        try:
            location = pyautogui.locateCenterOnScreen('imgs/video-track-1.png', confidence=0.9)
            if location:
                break
        except Exception:
            pass
        time.sleep(1)
        
    if not location:
        raise RuntimeError("Could not find imgs/video-track-1.png on screen")
        
    x, y = location
    print(f"Log: Found video track at ({x}, {y}). Clicking at ({x + 200}, {y}) to select the clip.")
    pyautogui.click(x + 200, y)
    
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', 'alt', 'm')

    find_and_click('imgs/silence-detection-titlebar.png', confidence=0.8, click=False)
    find_and_click('imgs/analyze-btn.png')

    try:
        print("Log: Processing...")
        while pyautogui.locateOnScreen('imgs/processing-screen.png', confidence=0.8):
            time.sleep(2)
    except pyautogui.ImageNotFoundException:
        pass

    # 6. Finish & Wait for Window Close
    print("Log: Finalizing...")
    find_and_click('imgs/finish-and-replace-btn.png')
    
    # Wait until the titlebar of the silence window is GONE
    timeout = 15
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            if not pyautogui.locateOnScreen('imgs/silence-detection-titlebar.png', confidence=0.8):
                break
            time.sleep(1)
        except pyautogui.ImageNotFoundException:
            break

    # 7. Save Project
    print(f"Log: Saving project to {SAVE_FULL_PATH}...")
    pyautogui.hotkey('ctrl', 's')

    # Wait for "Save As" window to appear and be active
    save_dialog_timeout = 10
    start_time = time.time()
    dialog_found = False

    while time.time() - start_time < save_dialog_timeout:
        windows = pyautogui.getWindowsWithTitle('Save As')
        if windows:
            dialog = windows[0]
            dialog.activate()  # Force focus
            dialog_found = True
            time.sleep(0.5) # Small buffer for focus to take hold
            break
        time.sleep(0.5)

    if not dialog_found:
        raise RuntimeError("Save As dialog never appeared or was not found.")

    time.sleep(1)  # Extra buffer for the dialog to fully render
    pyautogui.hotkey('alt', 'n')  # Standard Windows shortcut to focus 'File name:' input
    time.sleep(0.5)

    pyautogui.write(SAVE_FULL_PATH)
    time.sleep(0.5)
    pyautogui.press('enter')
    
    # Handle "Overwrite?" dialog if it exists
    time.sleep(1)
    pyautogui.press('y')

    print("Log: Automation Finished Successfully.")
    notify('Filmora Automation', f'Project "{PROJECT_NAME}" saved.')

if __name__ == "__main__":
    run_automation()