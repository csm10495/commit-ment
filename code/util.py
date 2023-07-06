import stat
import shutil
import os
import pathlib
import json
import tempfile
import subprocess
import threading
import sys
import io

SUBPROCESS_AS_SHELL = (os.name != 'nt')

def _del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)


def rmtree(path):
    shutil.rmtree(path, onerror=_del_rw)


def gettempdir() -> pathlib.Path:
    return pathlib.Path(os.environ.get('TMP') or os.environ.get('TEMP') or tempfile.gettempdir()).resolve()


def check_call(cmd, cwd=None):
    no_output = os.environ.get('SUBPROCESS_NO_OUTPUT')
    if not no_output:
        print(f'Running command: {cmd}')

    proc = subprocess.Popen(cmd, cwd=cwd, shell=SUBPROCESS_AS_SHELL, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stdout = io.BytesIO()
    stderr = io.BytesIO()

    def handle_stdout():
        for c in iter(lambda: proc.stdout.read(1), b""):
            stdout.write(c)
            if not no_output:
                sys.stdout.buffer.write(c)

    def handle_stderr():
        for ci in iter(lambda: proc.stderr.read(1), b""):
            stderr.write(ci)
            if not no_output:
                sys.stderr.buffer.write(ci)

    stdout_thread = threading.Thread(target=handle_stdout)
    stdout_thread.start()
    stderr_thread = threading.Thread(target=handle_stderr)
    stderr_thread.start()

    stdout_thread.join()
    stderr_thread.join()

    retcode = proc.wait()
    if retcode:
        raise subprocess.CalledProcessError(retcode, cmd, output=stdout.getvalue().decode('utf-8'), stderr=stderr.getvalue().decode('utf-8'))

def check_json_call(cmd, cwd=None):
    kwargs = {}
    if os.environ.get('SUBPROCESS_NO_OUTPUT'):
        kwargs = dict(stderr=subprocess.DEVNULL)
    else:
        print(f'Running command: {cmd}')


    return json.loads(subprocess.check_output(cmd, cwd=cwd, shell=SUBPROCESS_AS_SHELL, **kwargs).decode('utf-8'))