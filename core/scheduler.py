"""
Task scheduler — run agent tasks on a cron or interval schedule.
Built on APScheduler (in-process, no external broker needed).

Jobs are persisted to data/scheduler_jobs.json so they survive restarts.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_JOBS_FILE = Path(__file__).resolve().parent.parent / "data" / "scheduler_jobs.json"


class SchedulerJob:
    def __init__(
        self,
        job_id: str,
        name: str,
        agent: str,
        prompt: str,
        trigger: str,       # "interval" | "cron" | "date"
        trigger_args: dict,
        enabled: bool = True,
    ):
        self.job_id       = job_id
        self.name         = name
        self.agent        = agent
        self.prompt       = prompt
        self.trigger      = trigger
        self.trigger_args = trigger_args
        self.enabled      = enabled
        self.last_run: Optional[str]    = None
        self.last_result: Optional[str] = None
        self.run_count: int             = 0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "name": self.name, "agent": self.agent,
            "prompt": self.prompt, "trigger": self.trigger,
            "trigger_args": self.trigger_args, "enabled": self.enabled,
            "last_run": self.last_run, "last_result": self.last_result,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SchedulerJob":
        j = cls(
            job_id=d["job_id"], name=d["name"], agent=d["agent"],
            prompt=d["prompt"], trigger=d["trigger"],
            trigger_args=d.get("trigger_args", {}), enabled=d.get("enabled", True),
        )
        j.last_run    = d.get("last_run")
        j.last_result = d.get("last_result")
        j.run_count   = d.get("run_count", 0)
        return j


class AgentScheduler:
    def __init__(self) -> None:
        self._sched  = None
        self._jobs: dict[str, SchedulerJob] = {}
        self._agent_fn: Optional[Callable]  = None
        self._load()

    def set_agent_fn(self, fn: Callable) -> None:
        """Register the coroutine that runs agent tasks."""
        self._agent_fn = fn

    def start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._sched = AsyncIOScheduler()
            self._sched.start()
            for job in self._jobs.values():
                if job.enabled:
                    self._register_apscheduler_job(job)
            logger.info("Scheduler started with %d jobs", len(self._jobs))
        except ImportError:
            logger.warning("APScheduler not installed — scheduler disabled. pip install apscheduler")

    def shutdown(self) -> None:
        if self._sched:
            self._sched.shutdown(wait=False)

    def add_job(
        self,
        name: str,
        agent: str,
        prompt: str,
        trigger: str,
        trigger_args: dict,
    ) -> SchedulerJob:
        job = SchedulerJob(
            job_id=str(uuid.uuid4()),
            name=name, agent=agent, prompt=prompt,
            trigger=trigger, trigger_args=trigger_args,
        )
        self._jobs[job.job_id] = job
        if self._sched and job.enabled:
            self._register_apscheduler_job(job)
        self._save()
        return job

    def remove_job(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        if self._sched:
            try:
                self._sched.remove_job(job_id)
            except Exception:
                pass
        del self._jobs[job_id]
        self._save()
        return True

    def toggle_job(self, job_id: str) -> Optional[bool]:
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.enabled = not job.enabled
        if self._sched:
            if job.enabled:
                self._register_apscheduler_job(job)
            else:
                try:
                    self._sched.remove_job(job_id)
                except Exception:
                    pass
        self._save()
        return job.enabled

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()]

    def get_job(self, job_id: str) -> Optional[SchedulerJob]:
        return self._jobs.get(job_id)

    def _register_apscheduler_job(self, job: SchedulerJob) -> None:
        if not self._sched:
            return
        try:
            self._sched.remove_job(job.job_id)
        except Exception:
            pass

        trigger_kwargs = dict(job.trigger_args)
        self._sched.add_job(
            self._run_job,
            trigger=job.trigger,
            id=job.job_id,
            kwargs={"job_id": job.job_id},
            **trigger_kwargs,
            misfire_grace_time=60,
        )

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return
        logger.info("Running scheduled job: %s (%s)", job.name, job_id)
        job.last_run = datetime.utcnow().isoformat()
        job.run_count += 1
        try:
            if self._agent_fn:
                result = await self._agent_fn(job.prompt, job.agent)
                job.last_result = str(result)[:500]
            else:
                job.last_result = "No agent function registered"
        except Exception as exc:
            job.last_result = f"Error: {exc}"
            logger.exception("Scheduled job %s failed", job_id)
        self._save()

    def _save(self) -> None:
        try:
            _JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _JOBS_FILE.write_text(json.dumps([j.to_dict() for j in self._jobs.values()], indent=2))
        except Exception as exc:
            logger.warning("Scheduler save failed: %s", exc)

    def _load(self) -> None:
        if not _JOBS_FILE.exists():
            return
        try:
            for d in json.loads(_JOBS_FILE.read_text()):
                j = SchedulerJob.from_dict(d)
                self._jobs[j.job_id] = j
            logger.info("Loaded %d scheduled jobs", len(self._jobs))
        except Exception as exc:
            logger.warning("Scheduler load failed: %s", exc)


_scheduler: Optional[AgentScheduler] = None


def get_scheduler() -> AgentScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler
