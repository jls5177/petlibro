"""Import every PETLIBRO platform against an installed Home Assistant version."""

from __future__ import annotations

import importlib

from custom_components.petlibro.const import PLATFORMS


def main() -> None:
    """Import the integration and every declared platform."""
    modules = [
        "custom_components.petlibro",
        "custom_components.petlibro.pets.entity",
        *(f"custom_components.petlibro.{platform}" for platform in PLATFORMS),
    ]
    for module in modules:
        importlib.import_module(module)
    print(f"Imported {len(modules)} PETLIBRO modules")


if __name__ == "__main__":
    main()
