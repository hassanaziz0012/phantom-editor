# IMPORTANT: IF RUNNING FROM WSL, USE "python.exe" SO YOU RUN THE WINDOWS VERSION OF PYTHON.

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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def img(name):
    return os.path.join(BASE_DIR, "imgs", name)

# 1. Path Extraction
video_path = Path(FILE_TO_IMPORT)
PROJECT_NAME = video_path.parent.name
# Construct absolute Windows paths for Filmora (which runs on Windows)
FILE_TO_IMPORT = rf"C:\Users\hassa\Videos\YT Projects\{PROJECT_NAME}\{video_path.name}"
SAVE_FULL_PATH = rf"C:\Users\hassa\Videos\YT Projects\{PROJECT_NAME}\{PROJECT_NAME}"

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
    find_and_click(img('new-project-btn.png'))

    # 2. Editor UI
    print("Log: Waiting for Editor...")
    find_and_click(img('filmora-editor-blank-window.png'), confidence=0.7, retries=10)

    # 3. Import
    print("Log: Importing media...")
    pyautogui.hotkey('ctrl', 'i')
    time.sleep(2) 
    pyautogui.write(FILE_TO_IMPORT)
    pyautogui.press('enter')
    time.sleep(3) 

    # Save Project (First Save - dialog opens)
    print(f"Log: Saving project (first save) to {SAVE_FULL_PATH}...")
    pyautogui.hotkey('ctrl', 's')
    time.sleep(2)
    
    # Workaround for Windows focus bug: alt-tabbing away and back forces focus on the Save dialog
    pyautogui.hotkey('alt', 'tab')
    time.sleep(0.5)
    pyautogui.hotkey('alt', 'tab')
    time.sleep(1)

    pyautogui.write(SAVE_FULL_PATH)
    pyautogui.press('enter')
    
    # Handle "Overwrite?" dialog if it exists
    time.sleep(1)
    pyautogui.press('y')
    time.sleep(2) 

    # 4. Timeline
    print("Log: Adding to timeline...")
    pyautogui.click(425, 250) 
    time.sleep(0.5)
    pyautogui.hotkey('shift', 'i')
    time.sleep(1)
    pyautogui.press('enter')
    time.sleep(2) 

    # 5. Silence Detection
    print("Log: Running Silence Detection...")
    
    location = None
    for attempt in range(5):
        try:
            location = pyautogui.locateCenterOnScreen(img('video-track-1.png'), confidence=0.9)
            if location:
                break
        except Exception:
            pass
        time.sleep(1)
        
    if not location:
        raise RuntimeError(f"Could not find {img('video-track-1.png')} on screen")
        
    x, y = location
    print(f"Log: Found video track at ({x}, {y}). Clicking at ({x + 200}, {y}) to select the clip.")
    pyautogui.click(x + 200, y)
    
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', 'alt', 'm')

    find_and_click(img('silence-detection-titlebar.png'), confidence=0.8, click=False)
    find_and_click(img('analyze-btn.png'))

    try:
        print("Log: Processing...")
        while pyautogui.locateOnScreen(img('processing-screen.png'), confidence=0.8):
            time.sleep(2)
    except pyautogui.ImageNotFoundException:
        pass

    # 6. Finish & Wait for Window Close
    print("Log: Finalizing...")
    
    window_closed = False
    for attempt in range(3):
        find_and_click(img('finish-and-replace-btn.png'))
        
        # Wait a bit for the window to potentially close
        timeout = 5
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if not pyautogui.locateOnScreen(img('silence-detection-titlebar.png'), confidence=0.8):
                    window_closed = True
                    break
                time.sleep(1)
            except pyautogui.ImageNotFoundException:
                window_closed = True
                break
                
        if window_closed:
            break
        else:
            print(f"Log: Silence window still open (attempt {attempt + 1}/3). Retrying click...")

    if not window_closed:
        raise RuntimeError("Silence detection window did not close after clicking 'Finish and Replace' 3 times.")

    # 7. Save Project
    print("Log: Saving project...")
    pyautogui.hotkey('ctrl', 's')
    time.sleep(2)

    print("Log: Automation Finished Successfully.")
    notify('Filmora Automation', f'Project "{PROJECT_NAME}" saved.')

if __name__ == "__main__":
    run_automation()