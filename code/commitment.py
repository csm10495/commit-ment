from __future__ import annotations


import argparse
import os
import time

from branch import Branch
from worker import start_job_worker, ThreadWorker, ProcessWorker # Must leave ThreadWorker/ProcessWorker
from job import NewBranchThrashJob, MergeRemoteBranchesJob, MergePullRequestsJob, PullRequestCreatorJob
from signals import SigintCatcher



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-w', '--commit-workers', type=int, default=None)
    parser.add_argument('-c', '--max-commits-per-branch', type=int, default=1000)
    parser.add_argument('--merge-branches', action='store_true')
    parser.add_argument('--merge-prs', action='store_true')
    parser.add_argument('--create-prs', action='store_true')
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--worker-continue-on-exception', action='store_true')
    parser.add_argument('-s', '--seconds', type=int, default=60)
    parser.add_argument('-t', '--worker-type', type=str, default='process')
    parser.add_argument('-q', '--quiet', action='store_true')
    args = parser.parse_args()

    if args.quiet:
        os.environ['SUBPROCESS_NO_OUTPUT'] = '1'

    if args.worker_continue_on_exception:
        os.environ['WORKER_CONTINUE_ON_EXCEPTION'] = '1'

    if args.clean:
        Branch.clean_up_local_clones()

    workers = []

    worker_class = globals()[f'{args.worker_type.title()}Worker']
    print(f"Using worker class: {worker_class.__name__}")

    if args.commit_workers is not None:
        for _ in range(args.commit_workers):
            workers.append(start_job_worker(NewBranchThrashJob(commits_per_branch=args.max_commits_per_branch), worker_class))

    if args.merge_branches:
        input("Are you sure you want to merge-branches? .. Using --merge-prs and --create-prs is recommended instead. Press enter to continue")
        workers.append(start_job_worker(MergeRemoteBranchesJob(), worker_class))

    if args.merge_prs:
        workers.append(start_job_worker(MergePullRequestsJob(), worker_class))

    if args.create_prs:
        workers.append(start_job_worker(PullRequestCreatorJob(), worker_class))

    if workers:
        sigint_catcher = SigintCatcher()
        sigint_catcher.hook()

        try:
            death_time = time.time() + args.seconds
            while time.time() < death_time and not sigint_catcher.is_interrupted():
                if not workers:
                    print("... all workers died early")
                    break

                for w in workers:
                    if not w.is_alive():
                        w.join()

                        print(f"Worker died early: {w}.. stopping others")
                        raise KeyboardInterrupt()

                time.sleep(1)
        except KeyboardInterrupt:
            print("Keyboard Interrupt!")
        finally:
            print("Requesting all workers stop")
            for w in workers:
                w.request_stop()

            print("Joining all workers")
            for w in workers:
                w.join()
    else:
        print("No workers were started")