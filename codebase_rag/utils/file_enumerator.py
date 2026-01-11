"""
File enumeration utility for efficient single-pass filesystem traversal.

This module provides the FileEnumerator class which performs a single rglob
traversal and caches the results, providing both directories and files.
This eliminates duplicate filesystem scans between structure processing
and file processing phases.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .. import logs as ls
from .path_utils import should_skip_path

if TYPE_CHECKING:
    pass


class FileEnumerator:
    """
    Efficient file enumeration with single-pass traversal.

    Performs a single rglob("*") traversal and caches the results,
    providing separate access to directories and files. This eliminates
    the need for multiple filesystem scans during graph generation.

    Usage:
        enumerator = FileEnumerator(repo_path)
        enumerator.enumerate(exclude_paths, include_paths)

        for directory in enumerator.directories:
            # Process directories
            ...

        for filepath in enumerator.files:
            # Process files
            ...
    """

    def __init__(self, repo_path: Path) -> None:
        """
        Initialize the file enumerator.

        Args:
            repo_path: Root path of the repository to enumerate
        """
        self.repo_path = repo_path
        self._directories: list[Path] = []
        self._files: list[Path] = []
        self._enumerated = False

    def enumerate(
        self,
        exclude_paths: frozenset[str] | None = None,
        include_paths: frozenset[str] | None = None,
    ) -> None:
        """
        Perform filesystem enumeration.

        This method performs a single rglob traversal and separates the
        results into directories and files, filtering based on the
        provided include/exclude paths.

        Args:
            exclude_paths: Paths to exclude from enumeration
            include_paths: If set, only include paths matching these patterns
        """
        if self._enumerated:
            logger.debug(ls.FILE_ENUM_ALREADY_DONE)
            return

        directories: set[Path] = {self.repo_path}
        files: list[Path] = []

        for path in sorted(self.repo_path.rglob("*")):
            if should_skip_path(
                path,
                self.repo_path,
                exclude_paths=exclude_paths,
                include_paths=include_paths,
            ):
                continue

            if path.is_dir():
                directories.add(path)
            elif path.is_file():
                files.append(path)

        # Sort directories for deterministic processing order
        self._directories = sorted(directories)
        self._files = files  # Already sorted from sorted(rglob)
        self._enumerated = True

        logger.debug(
            ls.FILE_ENUM_COMPLETE.format(
                dirs=len(self._directories), files=len(self._files)
            )
        )

    @property
    def directories(self) -> list[Path]:
        """
        Get enumerated directories.

        Returns:
            List of directory paths, sorted for deterministic processing

        Raises:
            RuntimeError: If enumerate() has not been called
        """
        if not self._enumerated:
            raise RuntimeError("enumerate() must be called before accessing directories")
        return self._directories

    @property
    def files(self) -> list[Path]:
        """
        Get enumerated files.

        Returns:
            List of file paths, sorted for deterministic processing

        Raises:
            RuntimeError: If enumerate() has not been called
        """
        if not self._enumerated:
            raise RuntimeError("enumerate() must be called before accessing files")
        return self._files

    @property
    def is_enumerated(self) -> bool:
        """Check if enumeration has been performed."""
        return self._enumerated
