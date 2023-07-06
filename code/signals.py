import signal
import multiprocessing

class SigintCatcher:
    def __init__(self):
        self._event = multiprocessing.Event()

    def handler(self, signal, frame):
        print(f"Caught signal: {signal}, {frame}")
        self._event.set()

    def is_interrupted(self):
        return self._event.is_set()

    def hook(self):
        signal.signal(signal.SIGINT, self.handler)