# Filmora Automation

This directory contains scripts to automate Wondershare Filmora video editing workflows using UI automation.

## Scripts

### [filmora_silence_detection.py](file:///home/hassan/programming/phantom-editor/filmora-automation/filmora_silence_detection.py)
Automates Filmora's **Silence Detection** feature. It launches Filmora, imports a video clip, adds it to the timeline, runs the Silence Detection analysis, replaces the timeline clip with the silence-removed version, and saves the project.

* **Usage**: `python.exe filmora_silence_detection.py <path_to_mp4_file>`
  > [!IMPORTANT]
  > When running from **WSL**, you must invoke the script using `python.exe` (instead of `python`) to interact with the Windows GUI environment.
* **Requirements**:
  - Wondershare Filmora installed on Windows.
  - PyAutoGUI image-matching templates stored in the [imgs/](file:///home/hassan/programming/phantom-editor/filmora-automation/imgs) directory (used to identify buttons and windows).
  - The input file path must correspond to a folder structure containing `YT Projects/<project_name>/`.
* **Pipeline Steps**:
  1. **Launch**: Starts Filmora and opens a "New Project".
  2. **Import**: Imports the target MP4 file (`Ctrl + I`).
  3. **Initial Save**: Saves the project using a double `Alt + Tab` sequence to bypass the Windows Save dialog focus bug.
  4. **Timeline**: Adds the imported video clip to the timeline.
  5. **Silence Detection**: Opens the silence detection window (`Ctrl + Alt + M`), clicks "Analyze", and waits for the processing window to close.
  6. **Finalize**: Clicks "Finish and Replace" to update the timeline, and saves the project again.
