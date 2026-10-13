"""Step definitions and the loop that runs them.

Each step declares how to tell it is already done; the runner owns skipping,
timing, status recording, background execution and failure attribution, so
the step functions only do their actual work.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from utils import print_info, print_success, print_warning

from pipeline.common import PipelineError, is_valid_file
from pipeline.context import Outputs, PipelineContext


@dataclass(frozen=True)
class Step:
    index: int
    name: str  # short name for summary.md and the timing table
    title: str  # banner title
    description: str  # line in the pre-run overview
    run: Callable[[PipelineContext], None]
    # Files whose presence means the step is done (used by the default skip rule).
    outputs: Callable[[Outputs], tuple[Path, ...]] = lambda outputs: ()
    # Replaces the default skip rule. Returns why the step is skipped, or None to run it.
    skip_check: Callable[[PipelineContext, bool], str | None] | None = None
    # Whether running this step forces every later step to re-run.
    invalidates_downstream: bool = True
    # Run on a worker thread so its (network) latency overlaps later steps.
    background: bool = False
    # Background steps that must have finished before this step starts.
    waits_for: tuple[int, ...] = ()

    def skip_reason(self, ctx: PipelineContext, force: bool) -> str | None:
        if self.skip_check is not None:
            return self.skip_check(ctx, force)
        paths = self.outputs(ctx.outputs)
        if not force and paths and all(is_valid_file(p) for p in paths):
            return f"already complete -> {paths[0].name}"
        return None


def plan_steps(steps: list[Step], ctx: PipelineContext) -> dict[int, str | None]:
    """Predict each step's skip reason (None = will run) before anything runs."""
    force = ctx.force
    plan = {}
    for step in steps:
        plan[step.index] = step.skip_reason(ctx, force)
        if plan[step.index] is None and step.invalidates_downstream:
            force = True
    return plan


class BackgroundTask:
    """Run one step on a worker thread, remembering its error and run time.

    ``elapsed`` covers only the work inside the thread: spawn-to-join wall time
    would also absorb the steps it overlapped.
    """

    def __init__(self, step: Step, ctx: PipelineContext) -> None:
        self.step = step
        self.elapsed = 0.0
        self.error: BaseException | None = None
        self.joined = False
        self._thread = threading.Thread(name=f"step{step.index}", target=self._run, args=(ctx,))

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        if self._thread.is_alive():
            print_info(f"Waiting for background Step {self.step.index} ({self.step.name}) to finish...")
        self._thread.join()
        self.joined = True

    def _run(self, ctx: PipelineContext) -> None:
        start = time.perf_counter()
        try:
            self.step.run(ctx)
        except BaseException as exc:
            self.error = exc
        finally:
            self.elapsed = time.perf_counter() - start


def _attribute(step: Step, exc: BaseException) -> BaseException:
    """Prefix a PipelineError with the step it came from."""
    if isinstance(exc, PipelineError):
        return PipelineError(f"Step {step.index} ({step.name}) failed: {exc}", exc.exit_code)
    return exc


class PipelineRunner:
    def __init__(self, steps: list[Step], ctx: PipelineContext) -> None:
        self.steps = steps
        self.ctx = ctx
        self.tasks: dict[int, BackgroundTask] = {}
        # (step, start time) of the foreground step currently running.
        self._in_flight: tuple[Step, float] | None = None

    def run(self) -> None:
        try:
            for step in self.steps:
                for index in step.waits_for:
                    self._join(index)
                self._run_step(step)
            for index in list(self.tasks):
                self._join(index)
        except BaseException:
            self._record_abort()
            raise

    def _record(self, step: Step, status: str, duration: float) -> None:
        self.ctx.summary.record_step(step.index, step.name, status, duration)

    def _run_step(self, step: Step) -> None:
        print_info(f"\n--- [Step {step.index}/{len(self.steps)}] {step.title} ---")
        reason = step.skip_reason(self.ctx, self.ctx.force)
        if reason is not None:
            print_success(f"[SKIP] Step {step.index}: {reason}")
            self._record(step, "skipped", 0.0)
            return

        if step.invalidates_downstream:
            self.ctx.force = True

        if step.background:
            task = BackgroundTask(step, self.ctx)
            self.tasks[step.index] = task
            task.start()
            print_info(f"Step {step.index} ({step.name}) running in the background.")
            return

        start = time.perf_counter()
        self._in_flight = (step, start)
        try:
            step.run(self.ctx)
        except PipelineError as exc:
            raise _attribute(step, exc) from exc
        self._in_flight = None
        self._record(step, "ran", time.perf_counter() - start)
        print_success(f"[DONE] Step {step.index} ({step.name})")

    def _join(self, index: int) -> None:
        task = self.tasks.get(index)
        if task is None or task.joined:
            return
        task.join()
        if task.error is not None:
            self._record(task.step, "failed", task.elapsed)
            raise _attribute(task.step, task.error) from task.error
        self._record(task.step, "ran", task.elapsed)
        print_success(f"[DONE] Step {index} ({task.step.name}, background)")

    def _record_abort(self) -> None:
        """Record the failed step, let background work finish, and mark the rest skipped."""
        if self._in_flight is not None:
            step, start = self._in_flight
            self._record(step, "failed", time.perf_counter() - start)
            self._in_flight = None

        # Let background work finish (e.g. so a transcription is kept for a
        # resumed run) and record how it went, without masking the original error.
        for task in self.tasks.values():
            if task.joined:
                continue
            task.join()
            if task.error is not None:
                print_warning(f"Background Step {task.step.index} ({task.step.name}) also failed: {task.error}")
                self._record(task.step, "failed", task.elapsed)
            else:
                self._record(task.step, "ran", task.elapsed)

        for step in self.steps:
            if step.index not in self.ctx.summary.steps:
                self._record(step, "skipped", 0.0)
