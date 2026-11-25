"""Sample: Set DURAI_PLANS_DIR to a folder. Call plans_dir. Get that folder. Leave it unset. Get the folder that holds the checkout."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def plans_dir():
    # Sample: Env DURAI_PLANS_DIR is /work/plans. Return /work/plans. The env is unset and the checkout is /work/plans/durai-x/durai. Return /work/plans.
    return Path(os.environ.get('DURAI_PLANS_DIR') or ROOT.parent.parent)


def human_reviewers():
    # Sample: Env DURAI_HUMAN_REVIEWERS is "Ana, Bo". Return durai-human-lead, operator, ana, bo. The env is unset. Return the first two.
    extra = [name.strip().lower() for name in os.environ.get('DURAI_HUMAN_REVIEWERS', '').split(',') if name.strip()]
    return ('durai-human-lead', 'operator') + tuple(extra)
