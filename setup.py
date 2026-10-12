"""Bundle authoritative repository resources during wheel construction."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


RESOURCE_PATTERNS = {"schemas": "*.json", "rules": "*.yaml", "ids": "*.ids"}
PROJECT = Path(__file__).resolve().parent


class BuildPyWithResources(build_py):
    def _resources(self):
        for directory, pattern in RESOURCE_PATTERNS.items():
            for source in sorted((PROJECT / directory).glob(pattern)):
                destination = Path(self.build_lib) / "layout_configurator" / "_data" / directory / source.name
                yield source, destination

    def run(self):
        super().run()
        for source, destination in self._resources():
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + [str(destination) for _, destination in self._resources()]


setup(cmdclass={"build_py": BuildPyWithResources})
