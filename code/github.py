from __future__ import annotations

import subprocess

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job import Job
    from branch import Branch

@dataclass
class PR:
    number: int
    title: str
    state: str
    author: str
    is_draft: bool
    head: str
    base: str

@contextmanager
def handle_gh_backoff(job: Job, branch: Branch, branch_name: str | None = None):
    job_name = type(job).__name__

    try:
        yield
    except subprocess.CalledProcessError as e:
        # move this to a method to call more often
        if 'No commits between' in e.stderr:
            if branch_name is None:
                raise ValueError("Got a no commits error, but no branch name was provided")
            print (f"No commits were found between branches (master..{branch_name}).. deleting the branch")
            try:
                branch.delete_remote_branch(branch_name)
            except subprocess.CalledProcessError as e:
                if branch_name in branch.list_remote_branches():
                    print(f"Failed to delete branch {branch_name}.. but it still exists.. raising")
                    raise
                else:
                    print(f"Failed to delete branch {branch_name}.. but its already raised.. ignoring")
        elif '502' in e.stderr and 'bug' in e.stderr:
            print (f"Unexpected 502 (GH bug). Though it doesn't appear to be a rate-limit: {job_name}")
        elif 'the merge commit cannot be cleanly created' in e.stderr:
            print (f"GH claims it can't make the merge commit.. likely delay in previous merge: {job_name}")
        elif 'API rate limit exceeded' in e.stderr:
            job.request_backoff(f"GH API rate limit exceeded: {job_name}", 60)
        else:
            job.request_backoff(f"Unkown error: {e}", 5)

