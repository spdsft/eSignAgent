"""PyInstaller-friendly launcher (no relative imports outside the package)."""
from esignagent.main import main

if __name__ == "__main__":
    raise SystemExit(main())
