"""
Seamless mainloop routines

Status:
For now, the mainloop is controlled by Qt timers
An overhaul to put it under asyncio control is most welcome.
"""

import sys
import time
from collections import deque
import threading
import asyncio
import contextlib
import traceback

ipython = None
try:
    import IPython
    ipython = IPython.get_ipython()
except ImportError:
    pass
MAINLOOP_FLUSH_TIMEOUT = 30 #maximum duration of a mainloop flush in ms

class WorkQueue:
    FAILSAFE_FLUSH_LATENCY = 50 #latency of flush in ms
    _ipython_registered = False
    def __init__(self):
        self._work = deque()
        self._priority_work = deque()
        self._flushing = False
        self._signal_processing = 0
        self._append_lock = threading.Lock()

    def append(self, work, priority=False):
        with self._append_lock:
            if priority:
                self._priority_work.append(work)
            else:
                self._work.append(work)

    def flush(self, timeout=None):
        if threading.current_thread() is not threading.main_thread():
            return

        if ipython is not None and not self._ipython_registered:
            # It is annoying to do again and again, but the first time it doesn't work... bug in IPython?
            ###self._ipython_registered = True
            ipython.enable_gui("seamless")

        ### NOTE: disabled the code below to avoid the hanging
        #    of equilibrate() inside work
        #   It remains to be seen if this has any negative effects
        #if self._flushing:
        #    return
        ### /NOTE
        if timeout is not None:
            timeout_time = time.time() + timeout/1000
        self._flushing = True
        work_count = 0
        works = (self._priority_work, self._work)
        if self._signal_processing > 0:
            works = (self._priority_work,)
        for w in works:
            while len(w):
                if timeout is not None:
                    if time.time() > timeout_time:
                        break
                work = w.popleft()
                try:
                    work_count += 1
                    work()
                except Exception:
                    traceback.print_exc()
                if work_count == 100 and not _signal_processing:
                    run_qt() # Necessary to prevent freezes in glwindow
                    work_count = 0
        #Whenever work is done, do an asyncio flush
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0))
        """
        loop.call_soon(lambda loop: loop.stop(), loop)
        if not loop.is_running():
            loop.run_forever()
        """

        #print("flush")
        if self._signal_processing == 0:
            run_qt()

        self._flushing = False

    def __len__(self):
        return len(self._work) + len(self._priority_work)

def asyncio_finish():
    try:
        loop = asyncio.get_event_loop()
        loop.stop()
        loop.run_forever()
    except RuntimeError:
        pass

workqueue = WorkQueue()
def mainloop():
    """Only run in non-IPython mode"""
    while 1:
        mainloop_one_iteration()

def mainloop_one_iteration(timeout=MAINLOOP_FLUSH_TIMEOUT/1000):
    workqueue.flush(timeout)
    time.sleep(workqueue.FAILSAFE_FLUSH_LATENCY/1000)


def test_qt():
    import PyQt5.QtCore, PyQt5.QtWidgets
    PyQt5.QtWidgets.QApplication(["  "])
    return True

qt_app = None
from multiprocessing import Process
def run_qt():
    global run_qt, qt_app
    if qt_app is None: 
        import multiprocessing
        if multiprocessing.get_start_method() != "fork":
            print("""Cannot test if Qt can be started
This is because forking is not possible, you are probably running under Windows
If you are running from terminal (instead of Jupyter), you could enable Qt manually (TODO)
""")
        else:
            p = Process(target=test_qt)
            p.start()
            p.join()
            if not p.exitcode:
                qt_app = PyQt5.QtWidgets.QApplication(["  "])
        if qt_app is None:
            msg = "Qt could not be started. Qt widgets will not work" #TODO: some kind of env variable to disable this warning
            print(msg,file=sys.stderr)
            run_qt = lambda: None
            return
    qt_app.processEvents()

try:
    import PyQt5.QtCore, PyQt5.QtWidgets
except ImportError:
    run_qt = lambda: None
