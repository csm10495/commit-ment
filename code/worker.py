import multiprocessing
import os
import threading
import traceback
import time

from typing import Type
from job import Job, JobTaskNeedsBackoff


class Worker:
    def __init__(self, job: Job):
        self._job = job
        self._stop_requested_event = multiprocessing.Event()

        super().__init__()

    def request_stop(self):
        self._stop_requested_event.set()

    def run(self):
        try:
            self._job.setup()
            try:
                while not self._stop_requested_event.is_set() and not self._job.is_failed():
                    try:
                        self._job.do_single_task()
                    except JobTaskNeedsBackoff as ex:
                        print(f"Job requested backoff: {ex}")
                        self.sleep_for_backoff(ex.seconds)
                    except Exception as ex:
                        if os.environ.get('WORKER_CONTINUE_ON_EXCEPTION'):
                            print(f"do_single_task raised an unhandled exception: {ex}")
                            traceback.print_exc()
                            print(f"Continuing because WORKER_CONTINUE_ON_EXCEPTION is set")
                        else:
                            raise
            finally:
                self._job.teardown()
        except BaseException:
            print("Exception made it to the outer exception check of run():")
            traceback.print_exc()

    def sleep_for_backoff(self, seconds: int):
        death_time = time.time() + seconds
        while time.time() < death_time:
            # if a stop is requested, stop backing off, we're going to exit anyways
            if self._stop_requested_event.is_set():
                break
            time.sleep(1)


class ThreadWorker(Worker, threading.Thread):
    pass


class ProcessWorker(Worker, multiprocessing.Process):
    pass


def start_job_worker(job: Job, worker_class: Type[Worker]) -> Worker:
    w = worker_class(job)
    w.start()
    return w

