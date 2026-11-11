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

Any failure raises ColabUnavailable, and Step 4 falls back to the local encode.
"""

import shlex
import shutil
import subprocess
import threading
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


class ColabUnavailable(Exception):
    """The Colab encode can't run or failed; the caller encodes locally instead."""


class ColabEncoder:
    """One T4 session for one pipeline run: start, upload, encode, download, stop."""

    def __init__(self, main_video: Path) -> None:
        self.main_video = main_video
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
            self.upload(self.main_video, self.remote_video)
        except ColabUnavailable as e:
            self._error = str(e)
        except Exception as e:  # noqa: BLE001 - never let the worker thread die silently
            self._error = f"{type(e).__name__}: {e}"

    # ----- transfers and remote commands -----

    def upload(self, local: Path, remote: str) -> None:
        size = local.stat().st_size
        with local.open("rb") as src:
            self._ssh_stream(f"cat > {shlex.quote(remote)}", stdin=src, label=f"upload of {local.name}")
        remote_size = self._remote_size(remote)
        if remote_size != size:
            raise ColabUnavailable(f"upload of {local.name} is incomplete ({remote_size} of {size} bytes)")

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

    def _colab(self, *args: str, timeout: float, stdin=None) -> str:
        try:
            result = subprocess.run(
                ["colab", *args], capture_output=True, text=True, timeout=timeout, check=False,
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ColabUnavailable(f"'colab {args[0]}' timed out after {timeout:.0f}s") from None
        except FileNotFoundError:
            raise ColabUnavailable("the colab CLI is not installed") from None
        if result.returncode != 0:
            raise ColabUnavailable(f"'colab {args[0]}' failed: {_last_line(result.stderr or result.stdout)}")
        return result.stdout

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
    ) -> None:
        """Upload ``extra_inputs``, run the encode remotely, download ``output``, stop the VM.

        ``build_cmd(paths, output, video_args, main_input_args)`` returns the
        ffmpeg argv with every local input mapped through ``paths``.
        """
        self.wait_ready()
        paths = {self.main_video: self.remote_video}
        for index, local in enumerate(extra_inputs, start=1):
            remote = f"{REMOTE_DIR}/input{index}{local.suffix}"
            print_info(f"Colab: uploading {local.name}...")
            self.upload(local, remote)
            paths[local] = remote

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
