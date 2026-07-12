# Recording Synchronization

This script automates synchronized recording between Wondershare Filmora screen recorder on your PC and the Pixel Camera app on your Android device connected via wireless debugging/ADB.

## Scripts

### [record_in_sync.py](../recording/record_in_sync.py)
Automates starting and stopping Filmora screen recorder and the Pixel Camera app at the same time.

* **Usage**:
  * **Start Recording**: `phantom record start` or `python.exe recording/record_in_sync.py start`
  * **Stop Recording**: `phantom record stop` or `python.exe recording/record_in_sync.py stop`
  > [!IMPORTANT]
  > When running from **WSL**, you must invoke the script using the `phantom` CLI wrapper (which handles path resolution and calls `python.exe`) or call `python.exe` directly to allow PyAutoGUI to interact with the Windows GUI environment.
* **Requirements**:
  - Wondershare Filmora installed and running on Windows.
  - Filmora screen recorder set to toggle recording using the **F9** hotkey.
  - Pixel device paired and set up for **ADB and Wireless Debugging** (you will be prompted to enter the IP address and port at runtime).
  - Pixel camera app opened and active on the screen.
* **Prerequisites Check**:
  - Prompts the user to enter the IP address and port of the Pixel device.
  - Verifies Pixel phone connectivity via ADB. If the device is paired but not connected, the script will automatically attempt to connect to it using `adb connect`.
  - For starting, prompts you to confirm if Filmora screen recorder and Pixel camera app are both open and ready.
* **Workflow Steps**:
  - **Dynamic Input**: Prompts you for the device's IP and port.
  - **ADB Device Check and Auto-Connect**: Runs a connection check and automatically attempts to establish a connection if the device is offline.
  - **Filmora Trigger**: Presses the `F9` hotkey to toggle Filmora screen recording.
  3. **Pixel Trigger**: Immediately sends a volume up key event (`input keyevent 24`) to trigger recording in the Pixel camera app.
