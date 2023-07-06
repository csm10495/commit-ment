import abc
import uuid
import subprocess

from branch import Branch, GIT_REPO_CLONE_URL
from util import check_call, gettempdir, rmtree
from github import handle_gh_backoff

class JobTaskNeedsBackoff(Exception):
    def __init__(self, msg: str, seconds: int, job_type: str):
        self.msg = msg
        self.seconds = seconds
        self.job_type = job_type

    def __str__(self):
        return f'<JobTaskNeedsBackoff ({self.job_type}): {self.msg} (backoff: {self.seconds}s)>'


class Job(abc.ABC):
    def __init__(self):
        self._failure_msg = None

    def setup(self):
        pass

    def teardown(self):
        pass

    def do_single_task(self):
        pass

    def mark_failed(self, msg: str):
        self._failure_msg = msg

    def is_failed(self) -> str:
        return bool(self._failure_msg)

    def request_backoff(self, msg: str, seconds: int):
        raise JobTaskNeedsBackoff(msg, seconds, type(self).__name__)


class NullJob(Job):
    def do_single_task(self):
        print("Doing a single task")

    def setup(self):
        print("Doing setup")

    def teardown(self):
        print("Doing teardown")


class ShallowCloneJob(Job):
    def __init__(self, branch_name: str | None = None):
        self.branch_name = branch_name or str(uuid.uuid4())
        Job.__init__(self)

    def setup(self):
        print(f"Creating a shallow clone for branch: {self.branch_name}")

        tmpdir = gettempdir() / f'commit-ment_{self.branch_name}'

        do_clone = True
        if tmpdir.is_dir():
            do_clone = False
            try:
                check_call('git clean -dfx', cwd=tmpdir)
                check_call('git reset --hard', cwd=tmpdir)
                check_call(f'git pull origin {self.branch_name} --ff', cwd=tmpdir)
            except subprocess.CalledProcessError:
                print("Failed to reset repo.. deleting it to reset")
                rmtree(tmpdir)
                do_clone = True

        if do_clone:
            check_call(f'git clone --depth 1 {GIT_REPO_CLONE_URL} "{tmpdir}"')

        try:
            check_call(f'git checkout -b {self.branch_name}', cwd=tmpdir)
        except subprocess.CalledProcessError:
            check_call(f'git checkout {self.branch_name}', cwd=tmpdir)

        self.branch_obj = Branch(tmpdir, self.branch_name)

    def teardown(self):
        self.branch_obj.push_remote_branch()
        rmtree(self.branch_obj.repo_path)


class NewBranchThrashJob(ShallowCloneJob):
    def __init__(self, commits_per_branch: int=1000):
        ShallowCloneJob.__init__(self)
        self._commits_per_branch = commits_per_branch
        self._commit_count = 0

    def do_single_task(self):
        self.branch_obj.increment_and_commit()
        self._commit_count += 1

        if self._commit_count >= self._commits_per_branch:
            self._push_and_start_new()


    def _push_and_start_new(self):
        print(f"Pushing: {self.branch_name} then starting a new branch")
        self.teardown()
        self.__init__(commits_per_branch=self._commits_per_branch)
        self.setup()


class MergeRemoteBranchesJob(ShallowCloneJob):
    def __init__(self):
        ShallowCloneJob.__init__(self, 'master')

    def do_single_task(self):
        remote_branches = self.branch_obj.list_remote_branches()
        remote_branches.remove('master')

        if remote_branches:
            for branch in remote_branches:
                print(f"Merging branch: {branch}")

                self.branch_obj.fetch_branch(branch)

                try:
                    self.branch_obj.merge(f'{branch}')
                except subprocess.CalledProcessError as err:
                    print(f"Failed to merge branch: {branch}.. {err}\n{err.stderr}\n{err.stdout}")

            print("Pushing merged to master")
            self.branch_obj.pull_and_push_remote_branch()

            print("Deleting remote branches")
            self.branch_obj.delete_remote_branches(remote_branches)


class MergePullRequestsJob(Job):
    def do_single_task(self):
        merged_pr = False

        branch = Branch.from_this_clone()

        # note we're not passing the branch_name here.. so we can't delete the branch from handle_gh_backoff
        with handle_gh_backoff(self, branch):
            prs = branch.get_my_prs(limit=1, state='open')
            if prs:
                print(f"Merging PR: {prs[0]} ({prs[0].head})")
                branch.merge_pr(prs[0].number, verify=True)
                merged_pr = True

        if not merged_pr:
            self.request_backoff("No PRs to merge", 30)


class PullRequestCreatorJob(Job):
    def do_single_task(self):
        created_pr = False

        branch = Branch.from_this_clone()

        remote_branches = branch.list_remote_branches()
        remote_branches.remove('master')

        for i in remote_branches:
            with handle_gh_backoff(self, branch, i):
                open_pr = branch.get_my_prs(limit=1, state='open', head=i)
                if not open_pr:
                    print(f"Creating PR for branch: {i}")
                    branch.create_pr_for_branch(i)
                    created_pr = True
                    break

                if branch.get_my_prs(limit=1, state='merged', head=i):
                    print(f"Branch is merged but not deleted? Deleting it: {i}")
                    branch.delete_remote_branch(i)

        if not created_pr:
            self.request_backoff("No PRs to create", 30)
