"""Build the frontend viewer bundle into ``src/scryo/static`` if it is absent."""

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        static = root / "src" / "scryo" / "static"
        bundled = static / "assets" / "index.js"
        force = bool(os.environ.get("SCRYO_BUILD_FRONTEND"))

        if bundled.exists() and not force:
            return

        frontend = root / "frontend"
        build_sh = frontend / "scripts" / "build.sh"
        if not build_sh.exists():
            raise RuntimeError(
                f"Viewer bundle missing at {static} and {build_sh} not found. "
                "Provide a prebuilt src/scryo/static or the frontend/ source."
            )

        self.app.display_info("scryo: building frontend viewer bundle...")
        subprocess.run(["bash", "scripts/build.sh"], cwd=frontend, check=True)

        dist = frontend / "packages" / "viewer" / "dist"
        if not (dist / "assets" / "index.js").exists():
            raise RuntimeError(f"Frontend build produced no viewer dist at {dist}")

        if static.exists():
            shutil.rmtree(static)
        shutil.copytree(dist, static)
        self.app.display_info(f"scryo: copied viewer dist -> {static}")
