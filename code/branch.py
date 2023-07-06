
from __future__ import annotations

import backoff
import contextlib
import uuid
import pathlib
import subprocess
import time

from typing import List

from collections.abc import Generator
from util import check_call, gettempdir, SUBPROCESS_AS_SHELL, rmtree, check_json_call
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from github import PR


GIT_REPO_CLONE_URL = 'git@github.com:csm10495/commit-ment.git'
THIS_DIR = pathlib.Path(__file__).parent.resolve()


class Branch:
    def __init__(self, repo_path: pathlib.Path, name: str | None=None) -> None:
        self.repo_path = repo_path
        self.name = name or self.get_branch_name()
        branches_dir = self.repo_path / 'branches'
        branches_dir.mkdir(parents=True, exist_ok=True)
        self.file = branches_dir / self.name
        self._last_index = None

    def get_branch_name(self) -> str:
        return subprocess.check_output('git rev-parse --abbrev-ref HEAD', cwd=str(self.repo_path), shell=SUBPROCESS_AS_SHELL).decode().strip()

    def get_live_index(self) -> int:
        try:
            self._last_index = int(self.file.read_text())
            return self._last_index
        except (FileNotFoundError, ValueError):
            # how might a ValueError happen?
            # ... int('').. we had enough room on disk to make an empty file.. but not write to it.
            # ... ... ouch
            return 0

    def get_index(self) -> int:
        '''
        Cache the index in memory, so we don't have to read from disk every time.
        '''
        return self._last_index or self.get_live_index()

    def set_index(self, idx: int) -> None:
        self.file.write_text(str(idx))
        self._last_index = idx

    def commit(self, idx: int) -> None:
        check_call(f'git add "{self.file}"', cwd=str(self.repo_path))
        check_call(f'git commit -m "Bumping idx -> {idx} for {self.name}"', cwd=str(self.repo_path))

    def increment_and_commit(self) -> None:
        idx = self.get_index() + 1
        self.set_index(idx)
        self.commit(idx)

    def swap_branch(self, name) -> None:
        self.name = name
        check_call(f'git switch "{self.name}"', cwd=str(self.repo_path))

    @classmethod
    @contextlib.contextmanager
    def create_clone_on_new_branch(cls, branch: None | str=None, full_clone: bool = False, reuse_clone:bool=False) -> Generator[Branch, None, None]:
        if branch is None:
            branch = str(uuid.uuid4())

        if full_clone:
            branch = 'master'

        TEMP = gettempdir()

        if reuse_clone and not full_clone:
            raise ValueError('Cannot reuse clone without full clone')

        if reuse_clone:
            tmpdir = TEMP / 'commit-ment_full_clone'
            delete = False
        else:
            tmpdir = TEMP / f'commit-ment_{uuid.uuid4()}'
            delete = True

        if not tmpdir.is_dir():
            args = '--depth 1' if not full_clone else ''
            check_call(f'git clone {args} "{GIT_REPO_CLONE_URL}" "{tmpdir}"')
        else:
            check_call(f'git reset --hard', cwd=str(tmpdir))
            check_call(f'git clean -dfx', cwd=str(tmpdir))
            check_call(f'git pull --ff origin {branch}', cwd=str(tmpdir))

        try:
            check_call(f'git checkout -b "{branch}"', cwd=str(tmpdir))
        except subprocess.CalledProcessError:
            check_call(f'git checkout "{branch}"', cwd=str(tmpdir))

        try:
            yield cls(tmpdir, branch)
        finally:
            if delete:
                rmtree(tmpdir)

    def _push_and_clean_up(self):
        if self.name == 'master':
            print(f"Attempting to pull/push for repo (master): {self.repo_path}")
            self.pull_and_push_remote_branch(ignore_pull_fail=True)
        else:
            print(f"Attempting to push for repo ({self.name}): {self.repo_path}")
            self.push_remote_branch()

        print(f"cleaning up repo post push: {self.repo_path}")
        rmtree(self.repo_path)

    @classmethod
    def clean_up_local_clones(cls):
        print("Cleaning up local clones (that were orphaned)")
        TEMP = gettempdir()
        results = []
        with ProcessPoolExecutor() as executor:
            for path in TEMP.glob('commit-ment_*-*'):
                if path.is_dir():
                    try:
                        b = Branch(path)
                    except subprocess.CalledProcessError:
                        print(f"Cleaning up invalid repo: {path}")
                        rmtree(path)
                        continue

                    results.append(executor.submit(b._push_and_clean_up))
                else:
                    print(f"Invalid path !?: {path}")

            for r in results:
                r.result()

    @classmethod
    def from_this_clone(cls) -> Branch:
        return Branch(THIS_DIR, "master")

    def merge_into_master(self) -> None:
        branch = str(self.name)
        if self.name == 'master':
            raise NameError('Cannot merge master into master')

        self.swap_branch('master')
        self.merge(branch)

        # go back to branch
        self.swap_branch(branch)

    @backoff.on_exception(backoff.expo, subprocess.CalledProcessError, max_tries=5, max_time=5)
    def merge(self, branch_name: str) -> None:
        check_call(f'git merge "{branch_name}" --ff --no-edit', cwd=str(self.repo_path))

    @backoff.on_exception(backoff.expo, subprocess.CalledProcessError, max_tries=2, max_time=5)
    def fetch_branch(self, branch_name: str) -> None:
        check_call(f'git fetch origin "{branch_name}"', cwd=str(self.repo_path))
        check_call(f'git branch "{branch_name}" FETCH_HEAD', cwd=str(self.repo_path))

    def push_remote_branch(self) -> None:
        check_call(f'git push -u origin "{self.name}"', cwd=str(self.repo_path))

    def pull(self) -> None:
        check_call(f'git pull -s recursive -X theirs origin {self.name}', cwd=str(self.repo_path))

    @backoff.on_exception(backoff.expo, subprocess.CalledProcessError, max_tries=10, max_time=5)
    def pull_and_push_remote_branch(self, ignore_pull_fail: bool = False) -> None:
        try:
            self.pull()
        except subprocess.CalledProcessError:
            print("Ignoring pull fail")
            if not ignore_pull_fail:
                raise

        self.push_remote_branch()

    def list_remote_branches(self):
        output = subprocess.check_output(f'git ls-remote --heads --quiet', cwd=str(self.repo_path), shell=SUBPROCESS_AS_SHELL).decode('utf-8')
        return [line.split()[-1].split('refs/heads/')[-1] for line in output.splitlines()]

    def delete_remote_branch(self, branch: str) -> None:
        check_call(f'git push origin --delete {branch}', cwd=str(self.repo_path))

    def delete_remote_branches(self, branches: list[str]) -> None:
        with ThreadPoolExecutor(max_workers=32) as executor:
            results = []
            for branch in branches:
                results.append(executor.submit(self.delete_remote_branch, branch))

            for r in results:
                r.result()

    def get_my_prs(self, limit: int=10, state:str='open', head:str|None=None, number: int|None=None) -> list[PR]:
        if number and head:
            raise ValueError("Can't give head and number at the same time")

        head_str = '' if head is None else f' --head "{head}"'

        raw_prs = check_json_call(f'gh pr list --state {state} {head_str} --json number,title,author,state,isDraft,headRefName,baseRefName -L {limit}')
        prs = []
        for p in raw_prs:
            #  -A csm10495 isn't working for some reason
            if p['author']['login'] == 'csm10495':
                if number is None or number == p['number']:
                    prs.append(PR(
                        number=p['number'],
                        title=p['title'],
                        author=p['author']['login'],
                        state=p['state'],
                        is_draft=p['isDraft'],
                        head=p['headRefName'],
                        base=p['baseRefName']))

        return prs

    def create_pr_for_branch(self, branch_name: None | str):
        branch_name = branch_name or self.get_branch_name()
        check_call(f'gh pr create --base master --body "auto pr" --title "auto pr" --head "{branch_name}"')

    def merge_pr(self, number: int, verify: bool = False):
        check_call(f'gh pr merge {number} --delete-branch --merge')
        if verify:
            print(f"Waiting for PR {number} to merge")
            while self.get_my_prs(limit=1, state='open', number=number):
                time.sleep(1)
            print(f"PR {number} merged")
