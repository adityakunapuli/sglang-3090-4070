"""CLI package — keeps the console entry points ``myhealth`` / ``anthem`` working."""

from myhealth_fhir.cli.main import anthem_main as anthem_main, main as main

__all__ = ["main", "anthem_main"]
