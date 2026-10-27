"""Shared pytest setup: force headless Qt so importing GUI modules never needs a
display, and anchor the repo root on sys.path so ``import vizgui`` resolves."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
