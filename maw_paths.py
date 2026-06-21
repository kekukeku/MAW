"""MAW path helpers: project root vs MAW_workflow contract directory."""

import os

WORKFLOW_DIR_NAME = "MAW_workflow"


def get_project_root(path: str) -> str:
    """Resolve and normalize the target project root directory."""
    return os.path.realpath(os.path.expanduser(path.strip()))


def get_workflow_root(project_path: str) -> str:
    """Return the MAW_workflow contract directory inside a target project."""
    return os.path.join(get_project_root(project_path), WORKFLOW_DIR_NAME)