"""Sample: Add a detached git worktree for commit C-1 of repo R-1. Write journal.py. Read it back. Close. The worktree folder is gone."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from .common import Conflict
from .strategy_subagents import as_bytes, package_digest

GIT = ['git', '-c', 'safe.directory=*']


def run_git(repo, *arguments):
    # Sample: Run "git -c safe.directory=* -C R-1 worktree list". Return its text. A non-zero exit raises Conflict with the git message.
    done = subprocess.run(GIT + ['-C', str(repo)] + list(arguments), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise Conflict('GIT_FAILED: git ' + ' '.join(arguments[:2]) + ': ' + (done.stderr or done.stdout).strip()[-300:])
    return done.stdout


class GitWorktreeWorkspace:
    def __init__(self, repo, base_commit, name, scratch=None, base=None):
        # Sample: Add a detached worktree of commit C-1 under the scratch folder. With base {journal.py: "x = 1"}, refuse a commit whose journal.py differs.
        self.repo, self.base_commit, self.name = str(repo), base_commit, name
        self.folder = Path(tempfile.mkdtemp(prefix='durai-git-' + re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '-', dir=scratch))
        self.root = self.folder / 'tree'
        self.paths, self.closed = set(), False
        try:
            run_git(self.repo, 'worktree', 'add', '--detach', str(self.root), base_commit)
        except Conflict:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise
        self.base_digest = None
        try:
            for path, value in (base or {}).items():
                if not self.target(path).is_file() or self.target(path).read_bytes() != as_bytes(value):
                    raise Conflict('BASE_MISMATCH: ' + path + ' in commit ' + base_commit + ' differs from the step base')
                self.paths.add(path)
            self.base_digest = package_digest(self.files())
        except Conflict:
            self.close()
            raise

    def target(self, path):
        # Sample: Read /workspace/journal.py. Return tree/workspace/journal.py. Refuse a path with "..".
        if '..' in path.split('/'):
            raise Conflict('Workspace path escapes the worktree: ' + path)
        return self.root / path.lstrip('/')

    def write(self, path, value):
        # Sample: Write /workspace/journal.py with "x = 2". Read it back as b"x = 2".
        target = self.target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(as_bytes(value))
        self.paths.add(path)

    def read(self, path):
        # Sample: Read /workspace/journal.py after a write of "x = 2". Return b"x = 2".
        return self.target(path).read_bytes()

    def files(self):
        # Sample: Return the base paths and written paths of the worktree as bytes. Leave other repository files out.
        return {path: self.target(path).read_bytes() for path in sorted(self.paths) if self.target(path).is_file()}

    def close(self):
        # Sample: Remove the worktree with git worktree remove --force. Prune. Delete the folder. Repeat close. Nothing happens.
        if self.closed:
            return
        self.closed = True
        try:
            run_git(self.repo, 'worktree', 'remove', '--force', str(self.root))
        except Conflict:
            pass
        try:
            run_git(self.repo, 'worktree', 'prune')
        except Conflict:
            pass
        shutil.rmtree(self.folder, ignore_errors=True)


def git_workspace_factory(repo, base_commit, scratch=None):
    # Sample: Build factory F-1 for repo R-1 and commit C-1. F-1("S-1.sub-1", base) returns a GitWorktreeWorkspace.
    def make(name, base):
        # Sample: Create the worktree for name S-1.sub-1 with the step base files.
        return GitWorktreeWorkspace(repo, base_commit, name, scratch, base)
    return make
