"""Lazy cached aggregates with one lock owner for a dependency tree."""
import threading


class CompositeTask:
    _state_lock = threading.RLock()

    def __init__(self, value=None, children=()):
        self.lock = self._state_lock
        self.children, self.parents = list(children), []
        self.leaf_value, self.dirty, self.cached, self.computations, self.revision = value, True, None, 0, 0
        for child in self.children:
            child.parents.append(self)

    def _invalidate(self):
        self.dirty = True
        self.revision += 1
        for parent in self.parents:
            parent._invalidate()

    def set(self, value):
        with self.lock:
            if self.children:
                raise ValueError('Set a leaf value')
            self.leaf_value = value
            self._invalidate()

    def result(self):
        with self.lock:
            if self.dirty:
                self.cached = sum(child.result() for child in self.children) if self.children else self.leaf_value
                self.dirty = False
                self.computations += 1
            return self.cached

    def snapshot(self):
        # Sample: Read parent P-1 with children 100 and 100. Return value 200 and the current revision.
        with self.lock:
            return self.result(), self.revision

    def publish(self, value, revision, sink):
        # Sample: Read P-1 at revision 3. Change C-2. Publish 200 at revision 3. Refuse the stale value.
        from .common import Conflict
        with self.lock:
            if revision != self.revision:
                raise Conflict('Aggregate revision changed before publication')
            sink(value, revision)
            return value
