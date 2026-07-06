"""Backward-compatible model imports. New code should import from models."""
from models import Maintenance, Scan, User, Vehicle

__all__ = ["User", "Vehicle", "Scan", "Maintenance"]
