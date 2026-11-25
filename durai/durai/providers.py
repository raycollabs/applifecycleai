"""Capability routes with pinned work and explicit provider draining."""
from contextlib import contextmanager
from dataclasses import dataclass
import threading
from .common import Conflict


@dataclass
class Registration:
    capability: str
    version: str
    provider: object
    references: int = 0
    draining: bool = False


class Providers:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries, self.routes = {}, {}

    def register(self, capability, version, provider):
        key = (capability, version)
        with self.lock:
            if key in self.entries:
                raise Conflict('Provider version is immutable')
            prior = self.routes.get(capability)
            if prior:
                self.entries[prior].draining = True
            self.entries[key] = Registration(capability, version, provider)
            self.routes[capability] = key

    @contextmanager
    def acquire(self, capability):
        with self.lock:
            entry = self.entries[self.routes[capability]]
            entry.references += 1
        try:
            yield entry.provider
        finally:
            with self.lock:
                entry.references -= 1

    @contextmanager
    def resolve(self, capability, version):
        # Sample: Pin run R-1 to V-1 after V-2 is registered. Return V-1. Refuse a removed V-1.
        with self.lock:
            entry = self.entries.get((capability, version))
            if entry is None:
                raise Conflict('Pinned provider version is unavailable: ' + capability + '@' + version)
            entry.references += 1
        try:
            yield entry.provider
        finally:
            with self.lock:
                entry.references -= 1

    def available(self, capability, version):
        # Sample: Register workflow-step SINGLE only. Ask SINGLE. Return True. Ask TEAM. Return False.
        with self.lock:
            return (capability, version) in self.entries

    def retire(self, capability, version, active_pins):
        # Sample: Count 1 durable attempt on V-1. Refuse removal. Count 0. Remove V-1.
        if active_pins:
            raise Conflict('Durable attempts still pin this provider version')
        self.deregister(capability, version)

    def deregister(self, capability, version):
        key = (capability, version)
        with self.lock:
            entry = self.entries[key]
            if entry.references:
                raise Conflict('Pinned work must drain before deregistration')
            if self.routes.get(capability) == key:
                del self.routes[capability]
            del self.entries[key]
