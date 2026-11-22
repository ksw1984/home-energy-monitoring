"""Compatibility entry point; use ``src.hems.app`` for HEMS deployment."""

from src.hems.app import main, run

__all__ = ["main", "run"]

if __name__ == "__main__":  # pragma: no cover
    main()
