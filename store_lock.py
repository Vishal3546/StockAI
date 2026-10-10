"""Cross-process, cross-thread, reentrant local-file lock (Windows/POSIX).

All StockAI store writers acquire the same lock. Suitable for one local host;
network filesystems/multiple hosts require a database. File remains on disk.
"""
import os
import threading

class ProcessRLock:
    def __init__(self, path):
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, 'depth', 0)
        try:
            if depth == 0:
                f = open(self.path, 'a+b')
                try:
                    if os.name == 'nt':
                        import msvcrt
                        f.seek(0, 2)
                        if f.tell() == 0:
                            f.write(b'0'); f.flush()
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except BaseException:
                    f.close()
                    raise
                self._local.file = f
            self._local.depth = depth + 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, *args):
        try:
            self._local.depth -= 1
            if self._local.depth == 0:
                f = self._local.file
                try:
                    if os.name == 'nt':
                        import msvcrt
                        f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                finally:
                    f.close()
                    del self._local.file
        finally:
            self._thread_lock.release()
