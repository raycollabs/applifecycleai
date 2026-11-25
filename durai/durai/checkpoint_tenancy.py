"""Sample: Store app A checkpoint C-1 for attempt T-1 1. Read it as app B. Refuse the read."""
from langgraph.checkpoint.base import BaseCheckpointSaver
from .common import Conflict


class TenantCheckpointer(BaseCheckpointSaver):
    def __init__(self, inner, ledger):
        # Sample: Wrap PostgresSaver C-1 with the app A ledger. Keep the C-1 serializer.
        super().__init__(serde=inner.serde)
        self.inner, self.ledger = inner, ledger

    def authorize(self, config):
        # Sample: Read thread T-1 1 as app A. Return the config. Read it as app B. Refuse it.
        thread = (config.get('configurable') or {}).get('thread_id')
        if not thread:
            raise Conflict('Checkpoint access needs an attempt thread')
        try:
            self.ledger.get(thread)
        except KeyError:
            raise Conflict('Checkpoint thread is not visible to this application') from None
        return config

    def get_tuple(self, config):
        # Sample: Get the latest C-1 tuple for thread T-1 1 after authorization.
        return self.inner.get_tuple(self.authorize(config))

    def list(self, config, *, filter=None, before=None, limit=None):
        # Sample: List C-1 and C-2 for thread T-1 1 after authorization.
        if config is None:
            raise Conflict('Checkpoint listing needs an attempt thread')
        return self.inner.list(self.authorize(config), filter=filter, before=before, limit=limit)

    def put(self, config, checkpoint, metadata, new_versions):
        # Sample: Put checkpoint C-2 for thread T-1 1 after authorization.
        return self.inner.put(self.authorize(config), checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=''):
        # Sample: Put two pending writes for thread T-1 1 after authorization.
        return self.inner.put_writes(self.authorize(config), writes, task_id, task_path)

    def delete_thread(self, thread_id):
        # Sample: Delete thread T-1 1 after the app A ledger shows it.
        self.authorize({'configurable': {'thread_id': thread_id}})
        return self.inner.delete_thread(thread_id)

    def get_next_version(self, current, channel):
        # Sample: Read version 3 of channel messages. Return the C-1 next version.
        return self.inner.get_next_version(current, channel)
