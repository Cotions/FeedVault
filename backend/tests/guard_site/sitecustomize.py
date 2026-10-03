"""Loaded by every Python a test starts (conftest.tool_guard puts this folder
on PYTHONPATH): the test's guard, in force in the child too."""
import json
import os

if os.environ.get("FEEDVAULT_TEST_GUARD"):
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "toolguard", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "toolguard.py"))
    _toolguard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_toolguard)
    _toolguard._child = True
    _toolguard.install(_toolguard.Guard(**json.loads(os.environ["FEEDVAULT_TEST_GUARD"])))
