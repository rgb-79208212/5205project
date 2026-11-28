"""Metadata compatibility adapter for the unchanged web service config endpoint."""

from core.skills import Skills


def discover_catalog():
    return Skills().catalog
