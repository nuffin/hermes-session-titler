"""Hermes plugin: hermes-session-titler — thin shell for dual symlink + pip install."""

try:
    from hermes_session_titler import register as _register  # pip-installed
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hermes_session_titler import register as _register  # directory plugin

register = _register  # expose at module level for plugin discovery
