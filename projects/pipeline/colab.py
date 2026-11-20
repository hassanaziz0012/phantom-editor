"""Run the Step 4 encode on a free Google Colab T4 (NVENC) instead of local x264.

Driven through Google's ``colab`` CLI (``uv tool install google-colab-cli``,
then ``colab whoami`` once to log in). The VM start (~2 min) and the raw.mp4
upload (the slow part: it is bound by the home uplink) run on a background
thread from the start of the pipeline, so they overlap Steps 1-3. Step 4 then
uploads the small audio inputs, runs the same ffmpeg command on the VM and
downloads final.mp4.

Files travel over ``colab ssh`` stdin/stdout: ``colab upload`` goes through
Jupyter's contents API, which fails on files this large. Colab allows only one
``colab ssh`` connection per runtime, so transfers run one at a time.

A dropped upload is resumed from the bytes already on the VM (UPLOAD_ATTEMPTS
tries). Any other failure raises ColabUnavailable, and Step 4 falls back to the
local encode, or stops with an error under --relight.

With ``relight=True`` (``--relight``) the encode also relights the face on the
T4: remote/relight.py sits between an NVDEC decode of the cut timeline and the
usual NVENC encode. Its setup (script, models, mediapipe) runs over ``colab
exec``, i.e. through the notebook kernel rather than ssh, so it overlaps the raw
upload. A relight failure raises RelightFailed with the VM still up, and Step 4
retries as a plain Colab encode.
"""

import base64
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from video_editing.utils import print_info, print_success, run_ffmpeg_with_progress

from projects.pipeline.ffmpeg import KEYFRAME_INTERVAL

SESSION = "phantom-encode"
GPU = "T4"
SSH_KEY = Path.home() / ".ssh" / "colab_ed25519"
REMOTE_DIR = "/content"
REMOTE_OUTPUT = f"{REMOTE_DIR}/final.mp4"
# Allocating a VM normally takes ~2 minutes.
NEW_SESSION_TIMEOUT = 600
COMMAND_TIMEOUT = 120
# A long ssh upload can drop mid-stream; each retry resumes from the bytes already on the VM.
UPLOAD_ATTEMPTS = 6
UPLOAD_RETRY_DELAY = 5

# T4 NVENC settings from the 2026-11-11 benchmark: VMAF 94.3 against 91.5 for
# the local x264 veryfast/crf 20, at a ~10% bigger file. 1 s keyframes as in
# X264_ARGS (the excalidraw-broll kit needs them).
NVENC_ARGS = [
    "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "27", "-b:v", "0",
    "-pix_fmt", "yuv420p", "-g", str(KEYFRAME_INTERVAL), "-keyint_min", str(KEYFRAME_INTERVAL),
]
# Decode on the GPU too: the VM has 2 vCPUs, and CPU decoding capped the
# encode at 2.2x realtime instead of 5.2x.
HWACCEL_ARGS = ["-hwaccel", "cuda"]
ENCODER_LABEL = f"colab {GPU} (h264_nvenc)"

# Face relight (--relight). The script runs only on the VM; see its docstring for the look.
RELIGHT_SCRIPT = Path(__file__).parent / "remote" / "relight.py"
REMOTE_RELIGHT_DIR = f"{REMOTE_DIR}/relight"
RELIGHT_MODELS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    "selfie_multiclass_256x256.tflite":
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/"
        "selfie_multiclass_256x256.tflite",
}
# Tested on the VM's Python 3.13 (2026-11-15). Locally (3.12) this release crashes on model load.
MEDIAPIPE_VERSION = "1.2.3"
RELIGHT_SETUP_TIMEOUT = 600
RELIGHT_READY = "RELIGHT_READY"

# Runs in the VM's notebook kernel via `colab exec`. Placeholders are filled with repr()s.
_RELIGHT_SETUP = """
import base64, os, subprocess, urllib.request
d = __DIR__
os.makedirs(d + "/models", exist_ok=True)
with open(d + "/relight.py", "wb") as f:
    f.write(base64.b64decode(__SCRIPT__))
for name, url in __MODELS__.items():
    path = d + "/models/" + name
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)
print("relight setup: installing mediapipe", flush=True)
subprocess.run(["python3", "-m", "pip", "install", "-q", "mediapipe==" + __MP__], check=True)
print("relight setup: fetching the matting model", flush=True)
subprocess.run(["python3", "-c", "import torch; torch.hub.load('PeterL1n/RobustVideoMatting', 'mobilenetv3', "
                "trust_repo=True, verbose=False)"], check=True)
print(__READY__, flush=True)
"""

# relight_cmds(paths, remote_output, video_args) -> (decode argv, encode argv, frame count, "WxH")
RelightCmds = Callable[[dict[Path, str], str, list[str]], tuple[list[str], list[str], int, str]]


class ColabUnavailable(Exception):
    """The Colab encode can't run or failed; the caller encodes locally instead."""


class RelightFailed(Exception):
    """The relight encode couldn't run or failed. The VM is still up for a plain Colab encode."""


class ColabEncoder:
    """One T4 session for one pipeline run: start, upload, encode, download, stop."""

    def __init__(self, main_video: Path, relight: bool = False) -> None:
        self.main_video = main_video
        self.relight = relight
        self.relit = False
        self._relight_thread: threading.Thread | None = None
        self._relight_error: str | None = None
        self._uploaded: dict[Path, str] = {}
        self.remote_video = f"{REMOTE_DIR}/raw{main_video.suffix or '.mp4'}"
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._started = False
        self._stopped = False
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ----- lifecycle -----

    def start(self) -> None:
        """Start the VM and upload the raw video in the background."""
        self._thread = threading.Thread(name="colab-prepare", target=self._prepare, daemon=True)
        self._thread.start()
        print_info(f"Colab: starting a {GPU} VM and uploading {self.main_video.name} in the background.")

    def wait_ready(self) -> None:
        """Block until the VM is up and the raw video uploaded; raise if that failed."""
        if self._thread is None:
            raise ColabUnavailable("the Colab VM was never started")
        if self._thread.is_alive():
            print_info("Colab: waiting for the VM and the raw video upload...")
        self._thread.join()
        if self._error:
            raise ColabUnavailable(self._error)

    def stop(self) -> None:
        """Release the VM (idempotent). Also aborts a transfer still in flight."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if not self._started:
            return
        try:
            self._colab("stop", "-s", SESSION, timeout=COMMAND_TIMEOUT)
            print_info("Colab: VM stopped.")
        except ColabUnavailable as e:
            print_info(f"Colab: could not stop the VM ({e}); it shuts down on its own once idle.")

    def _prepare(self) -> None:
        try:
            if shutil.which("colab") is None:
                raise ColabUnavailable("the colab CLI is not installed (uv tool install google-colab-cli)")
            self._ensure_ssh_key()
            # A session left over from a crashed run would hold the free T4.
            try:
                self._colab("stop", "-s", SESSION, timeout=COMMAND_TIMEOUT)
            except ColabUnavailable:
                pass
            with self._lock:
                if self._stopped:
                    raise ColabUnavailable("cancelled")
                self._started = True
            self._colab("new", "-s", SESSION, "--gpu", GPU, timeout=NEW_SESSION_TIMEOUT)
            if self.relight:
                # Over the notebook kernel, so it runs alongside the ssh upload below.
                self._relight_thread = threading.Thread(name="colab-relight-setup", target=self._setup_relight,
                                                        daemon=True)
                self._relight_thread.start()
            self.upload(self.main_video, self.remote_video)
        except ColabUnavailable as e:
            self._error = str(e)
        except Exception as e:  # noqa: BLE001 - never let the worker thread die silently
            self._error = f"{type(e).__name__}: {e}"

    def _setup_relight(self) -> None:
        """Put the relight script, its models and mediapipe on the VM (sets _relight_error on failure)."""
        code = (_RELIGHT_SETUP
                .replace("__DIR__", repr(REMOTE_RELIGHT_DIR))
                .replace("__SCRIPT__", repr(base64.b64encode(RELIGHT_SCRIPT.read_bytes()).decode()))
                .replace("__MODELS__", repr(RELIGHT_MODELS))
                .replace("__MP__", repr(MEDIAPIPE_VERSION))
                .replace("__READY__", repr(RELIGHT_READY)))
        fd, path = tempfile.mkstemp(prefix="phantom-relight-setup-", suffix=".py")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)
            out = self._colab("exec", "-s", SESSION, "-f", path, "--timeout", str(RELIGHT_SETUP_TIMEOUT),
                              timeout=RELIGHT_SETUP_TIMEOUT + 60)
            if RELIGHT_READY not in out:
                self._relight_error = f"setup did not finish: {_last_line(out)}"
        except ColabUnavailable as e:
            self._relight_error = f"setup failed: {e}"
        except Exception as e:  # noqa: BLE001 - never let the worker thread die silently
            self._relight_error = f"setup failed: {type(e).__name__}: {e}"
        finally:
            os.unlink(path)

    def _wait_relight(self) -> None:
        if self._relight_thread is None:
            raise RelightFailed("its setup never started")
        if self._relight_thread.is_alive():
            print_info("Colab: waiting for the face relight setup...")
        self._relight_thread.join()
        if self._relight_error:
            raise RelightFailed(self._relight_error)

    # ----- transfers and remote commands -----

    def upload(self, local: Path, remote: str) -> None:
        """Stream ``local`` to ``remote``, resuming from the remote size after a dropped connection.

        A multi-minute ``colab ssh`` stream can be cut mid-transfer ("Connection to
        colab-runtime closed by remote host"). Bytes that reached the VM are an
        exact prefix of the file, so each retry kills the old writer (it may still
        hold the single ssh slot) and appends from where it stopped.
        """
        size = local.stat().st_size
        # dd rather than `cat >`: the target path is in its argv, so a stale writer can be found by pkill.
        writer = f"dd of={shlex.quote(remote)} bs=1M status=none"
        offset = 0
        last_error = ""
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            if attempt > 1:
                print_info(f"Colab: upload of {local.name} dropped ({last_error}); "
                           f"resuming at {offset / 1e6:.0f} of {size / 1e6:.0f} MB "
                           f"(attempt {attempt}/{UPLOAD_ATTEMPTS})...")
            # The first attempt truncates, so a stale file from an earlier run can't leak in.
            shell_line = f"{writer} oflag=append conv=notrunc" if offset else writer
            try:
                with local.open("rb") as src:
                    src.seek(offset)
                    self._ssh_stream(shell_line, stdin=src, label=f"upload of {local.name}")
                remote_size = self._remote_size(remote)
            except ColabUnavailable as e:
                if self._stopped or attempt == UPLOAD_ATTEMPTS:
                    raise
                last_error = str(e)
                offset = self._recover_upload(remote, size)
                continue
            if remote_size == size:
                return
            last_error = f"only {remote_size} of {size} bytes arrived"
            if remote_size > size:
                break
            offset = remote_size
        raise ColabUnavailable(f"upload of {local.name} failed after {UPLOAD_ATTEMPTS} attempts: {last_error}")

    def _recover_upload(self, remote: str, size: int) -> int:
        """After a dropped upload: stop the stale remote writer and return the bytes already on the VM.

        A writer that outlived its connection could otherwise append after the
        size check. ssh first (the kernel may be busy with the relight setup for
        minutes); the dead session can briefly hold the only ssh slot, so it
        retries, then falls back to the notebook kernel.
        """
        # "[d]d" matches the dd process but not this shell's own command line.
        pattern = shlex.quote(f"[d]d of={shlex.quote(remote)}")
        quoted = shlex.quote(remote)
        shell_line = f"pkill -f -- {pattern}; sleep 1; echo UPLOADED_BYTES $(stat -c %s {quoted} 2>/dev/null || echo 0)"
        code = (
            "import os, subprocess, time\n"
            f"subprocess.run(['pkill', '-f', '--', {'[d]d of=' + shlex.quote(remote)!r}])\n"
            "time.sleep(1)\n"
            f"p = {remote!r}\n"
            "print('UPLOADED_BYTES', os.path.getsize(p) if os.path.exists(p) else 0)\n"
        )
        error: Exception | None = None
        for delay in (UPLOAD_RETRY_DELAY, 2 * UPLOAD_RETRY_DELAY, 4 * UPLOAD_RETRY_DELAY, 0):
            time.sleep(delay)
            try:
                if delay:
                    out = self._colab("ssh", "-s", SESSION, "-i", str(SSH_KEY), "--", shell_line,
                                      timeout=COMMAND_TIMEOUT)
                else:
                    out = self._colab("exec", "-s", SESSION, "--timeout", str(RELIGHT_SETUP_TIMEOUT),
                                      input_text=code, timeout=RELIGHT_SETUP_TIMEOUT + 60)
                done = int(out.split("UPLOADED_BYTES", 1)[1].split()[0])
                return done if 0 <= done <= size else 0
            except (ColabUnavailable, IndexError, ValueError) as e:
                error = e
        print_info(f"Colab: could not check the partial upload ({error}); restarting it from the beginning.")
        return 0

    def download(self, remote: str, local: Path) -> None:
        size = self._remote_size(remote)
        partial = local.with_name(f".{local.name}.colab-part")
        try:
            with partial.open("wb") as dst:
                self._ssh_stream(f"cat {shlex.quote(remote)}", stdout=dst, label=f"download of {local.name}")
            if partial.stat().st_size != size:
                raise ColabUnavailable(
                    f"download of {local.name} is incomplete ({partial.stat().st_size} of {size} bytes)"
                )
            partial.replace(local)
        finally:
            partial.unlink(missing_ok=True)

    def run_ffmpeg(
        self,
        argv: list[str],
        duration: float,
        description: str,
        stderr_line_callback: Callable[[str], None] | None,
    ) -> None:
        """Run an ffmpeg argv on the VM with the usual local progress bar.

        Several arguments after ``colab ssh --`` run as an exact argv, so the
        filtergraph needs no extra quoting. ffmpeg's stdout (where
        run_ffmpeg_with_progress asks for -progress) comes back byte-exact.
        """
        cmd = ["colab", "ssh", "-s", SESSION, "-i", str(SSH_KEY), "--", *argv]
        try:
            run_ffmpeg_with_progress(
                cmd, duration, description, stderr_line_callback=stderr_line_callback, raise_on_error=True,
            )
        except subprocess.CalledProcessError as e:
            raise ColabUnavailable(f"the remote ffmpeg exited with code {e.returncode}") from e

    def _remote_size(self, remote: str) -> int:
        out = self._colab(
            "ssh", "-s", SESSION, "-i", str(SSH_KEY), "--", f"stat -c %s {shlex.quote(remote)}",
            timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL,
        )
        try:
            return int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ColabUnavailable(f"could not read the size of {remote} on the VM") from None

    def _ssh_stream(self, shell_line: str, *, label: str, stdin=None, stdout=None) -> None:
        cmd = ["colab", "ssh", "-s", SESSION, "-i", str(SSH_KEY), "--", shell_line]
        with self._lock:
            if self._stopped:
                raise ColabUnavailable("cancelled")
            self._proc = subprocess.Popen(
                cmd,
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                stdout=stdout if stdout is not None else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        _, err = self._proc.communicate()
        if self._proc.returncode != 0:
            raise ColabUnavailable(f"{label} failed: {_last_line(err)}")

    def _colab(self, *args: str, timeout: float, stdin=None, input_text: str | None = None) -> str:
        stdin_kw = {"input": input_text} if input_text is not None else {
            "stdin": stdin if stdin is not None else subprocess.DEVNULL}
        try:
            result = subprocess.run(
                ["colab", *args], capture_output=True, text=True, timeout=timeout, check=False, **stdin_kw,
            )
        except subprocess.TimeoutExpired:
            raise ColabUnavailable(f"'colab {args[0]}' timed out after {timeout:.0f}s") from None
        except FileNotFoundError:
            raise ColabUnavailable("the colab CLI is not installed") from None
        if result.returncode != 0:
            raise ColabUnavailable(f"'colab {args[0]}' failed: {_last_line(result.stderr or result.stdout)}")
        return result.stdout

    def _encode_relit(self, relight_cmds: RelightCmds, paths: dict[Path, str], duration: float, description: str,
                      stderr_line_callback: Callable[[str], None] | None) -> None:
        """NVDEC decode of the cut -> remote/relight.py -> the usual NVENC encode, as one remote command."""
        if not self.relight:
            raise RelightFailed("the encoder was started without relight")
        self._wait_relight()
        decode, encode, frames, size = relight_cmds(paths, REMOTE_OUTPUT, NVENC_ARGS)
        argv = [
            "python3", f"{REMOTE_RELIGHT_DIR}/relight.py", "--models", f"{REMOTE_RELIGHT_DIR}/models",
            "--decode", json.dumps(decode), "--size", size, "--frames", str(frames), "--", *encode,
        ]
        try:
            # run_ffmpeg_with_progress puts -progress right after the encode argv's -y; the encoder
            # inherits relight.py's stdout, so the progress stream still reaches the local bar.
            self.run_ffmpeg(argv, duration, f"{description} + Face Relight [Colab {GPU}]", stderr_line_callback)
        except ColabUnavailable as e:
            raise RelightFailed(str(e)) from e
        self.relit = True

    @property
    def label(self) -> str:
        return f"{ENCODER_LABEL} + face relight" if self.relit else ENCODER_LABEL

    @staticmethod
    def _ensure_ssh_key() -> None:
        if SSH_KEY.is_file():
            return
        SSH_KEY.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "colab-cli", "-q", "-f", str(SSH_KEY)],
            check=True, stdin=subprocess.DEVNULL,
        )

    # ----- the Step 4 encode -----

    def encode(
        self,
        build_cmd: Callable[[dict[Path, str], str, list[str], list[str]], list[str]],
        extra_inputs: list[Path],
        output: Path,
        duration: float,
        description: str,
        stderr_line_callback: Callable[[str], None] | None,
        relight_cmds: RelightCmds | None = None,
    ) -> None:
        """Upload ``extra_inputs``, run the encode remotely, download ``output``, stop the VM.

        ``build_cmd(paths, output, video_args, main_input_args)`` returns the
        ffmpeg argv with every local input mapped through ``paths``. With
        ``relight_cmds`` the encode relights the face; if that fails,
        RelightFailed is raised and the VM stays up for a plain retry.
        """
        self.wait_ready()
        paths = {self.main_video: self.remote_video}
        for index, local in enumerate(extra_inputs, start=1):
            if local not in self._uploaded:   # a plain retry after a failed relight reuses them
                remote = f"{REMOTE_DIR}/input{index}{local.suffix}"
                print_info(f"Colab: uploading {local.name}...")
                self.upload(local, remote)
                self._uploaded[local] = remote
            paths[local] = self._uploaded[local]

        if relight_cmds is not None:
            self._encode_relit(relight_cmds, paths, duration, description, stderr_line_callback)
        else:
            argv = build_cmd(paths, REMOTE_OUTPUT, NVENC_ARGS, HWACCEL_ARGS)
            # ffmpeg would otherwise read the ssh channel's stdin as keyboard commands.
            argv.insert(1, "-nostdin")
            self.run_ffmpeg(argv, duration, f"{description} [Colab {GPU}]", stderr_line_callback)

        print_info(f"Colab: downloading {output.name}...")
        self.download(REMOTE_OUTPUT, output)
        print_success(f"Colab: {output.name} downloaded.")
        self.stop()


def _last_line(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    # Skip the CLI's update-check notice, which it prints on most calls.
    noise = ("A new version of Colab CLI", "Run 'colab update'", "enable_update_check")
    lines = [line for line in (text or "").splitlines() if line.strip() and not any(n in line for n in noise)]
    return lines[-1].strip() if lines else "no error output"
