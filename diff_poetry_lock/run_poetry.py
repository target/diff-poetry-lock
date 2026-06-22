import tempfile
from operator import attrgetter
from pathlib import Path
from re import search

import pydantic
from loguru import logger
from poetry.core.packages.package import Package
from poetry.packages import Locker

from diff_poetry_lock import __version__
from diff_poetry_lock.github_api import GithubApi
from diff_poetry_lock.logging_utils import configure_logging
from diff_poetry_lock.settings import Settings, determine_and_load_settings


def load_packages(filename: Path = Path("poetry.lock")) -> list[Package]:
    l_merged = Locker(Path(filename), {})
    return l_merged.locked_repository().packages


@pydantic.dataclasses.dataclass(config={"arbitrary_types_allowed": True})
class PackageSummary:
    name: str
    old_version: str | None = None
    new_version: str | None = None

    def not_changed(self) -> bool:
        return self.new_version == self.old_version

    def changed(self) -> bool:
        return not self.not_changed()

    def updated(self) -> bool:
        return self.new_version is not None and self.old_version is not None and self.changed()

    def added(self) -> bool:
        return self.new_version is not None and self.old_version is None

    def removed(self) -> bool:
        return self.new_version is None and self.old_version is not None

    @staticmethod
    def _version_core(version: str) -> str:
        match = search(r"\d+(?:\.\d+)*", version)
        if match is None:
            msg = f"Could not parse numeric version from '{version}'"
            raise ValueError(msg)
        return match.group(0)

    @classmethod
    def _version_tuple(cls, version: str) -> tuple[int, int, int]:
        parts = [int(part) for part in cls._version_core(version).split(".")]
        while len(parts) < 3:
            parts.append(0)
        return parts[0], parts[1], parts[2]

    def upgrade_type(self) -> str:
        if self.added():
            return "new"
        if self.removed():
            return "drop"
        if self.updated() and self.old_version is not None and self.new_version is not None:
            old_major, old_minor, old_patch = self._version_tuple(self.old_version)
            new_major, new_minor, new_patch = self._version_tuple(self.new_version)
            if new_major != old_major:
                return "major"
            if new_minor != old_minor:
                return "minor"
            if new_patch != old_patch:
                return "patch"
            return "patch"

        msg = "Inconsistent State"
        raise ValueError(msg)

    def table_row(self) -> str:
        action = "Updated"
        old_version = self.old_version or ""
        new_version = self.new_version or ""
        if self.added():
            action = "Added"
        elif self.removed():
            action = "Removed"

        return f"|{action}|{self.name}|{old_version}|{new_version}|{self.upgrade_type()}|"


def diff(old_packages: list[Package], new_packages: list[Package]) -> list[PackageSummary]:
    merged: dict[str, PackageSummary] = {}
    for package in old_packages:
        merged[package.pretty_name] = PackageSummary(name=package.pretty_name, old_version=package.full_pretty_version)
    for package in new_packages:
        if package.pretty_name not in merged:
            merged[package.pretty_name] = PackageSummary(
                name=package.pretty_name,
                new_version=package.full_pretty_version,
            )
        else:
            merged[package.pretty_name].new_version = package.full_pretty_version

    return list(merged.values())


def post_comment(api: GithubApi, comment: str | None) -> None:
    existing_comments = api.list_comments()

    if len(existing_comments) > 1:
        logger.warning("Found more than one existing comment, only updating first comment")

    existing_comment = existing_comments[0] if existing_comments else None
    api.upsert_comment(existing_comment, comment)


def format_comment(
    packages: list[PackageSummary],
    base_commit_hash: str | None = None,
    head_commit_hash: str | None = None,
) -> str | None:
    added = sorted([p for p in packages if p.added()], key=attrgetter("name"))
    removed = sorted([p for p in packages if p.removed()], key=attrgetter("name"))
    updated = sorted([p for p in packages if p.updated()], key=attrgetter("name"))
    not_changed = sorted([p for p in packages if p.not_changed()], key=attrgetter("name"))

    if len(added + removed + updated) == 0:
        return None

    change_count = len(added + removed + updated)
    comment = f"### Detected {change_count} changes to dependencies in Poetry lockfile\n\n"
    if base_commit_hash and head_commit_hash:
        comment += f"From base {base_commit_hash} to head {head_commit_hash}:\n\n"
    comment += "|Action|Package|Old version|New version|Upgrade type\n"
    comment += "|---|---|---|---|---|\n"
    summary_lines = [p.table_row() for p in added + removed + updated]
    comment += "\n".join(summary_lines)
    comment += (
        f"\n\n*({len(added)} added, {len(removed)} removed, {len(updated)} updated, {len(not_changed)} not changed)*"
    )
    if __version__:
        comment += f"\n\n<small>Generated by diff-poetry-lock {__version__}</small>\n\n"

    return comment


def load_lockfile(api: GithubApi, ref: str) -> list[Package]:
    file_contents = api.get_file(ref)
    with tempfile.NamedTemporaryFile(mode="wb", delete=True) as f:
        for chunk in file_contents.iter_content(chunk_size=1024):
            f.write(chunk)
        f.flush()

        return load_packages(Path(f.name))


def main() -> None:
    configure_logging()
    settings = determine_and_load_settings()
    do_diff(settings)


def do_diff(settings: Settings) -> None:
    api = GithubApi(settings)

    logger.debug("Starting diff with base_ref={} ref={}", settings.base_ref, settings.ref)

    logger.debug("Loading base lockfile...")
    base_packages = load_lockfile(api, settings.base_ref)
    logger.debug("Loaded {} base packages", len(base_packages))

    logger.debug("Loading head lockfile...")
    head_packages = load_lockfile(api, settings.ref)
    logger.debug("Loaded {} head packages", len(head_packages))

    logger.debug("Computing diff...")
    packages = diff(base_packages, head_packages)

    if not any(package.changed() for package in packages):
        summary = None
    else:
        head_commit_hash, base_commit_hash = api.resolve_commit_hashes()
        summary = format_comment(
            packages,
            base_commit_hash=base_commit_hash,
            head_commit_hash=head_commit_hash,
        )

    if summary:
        logger.debug("Generated summary with {} characters", len(summary))
        logger.debug("\n=== DIFF SUMMARY ===\n{}\n====================\n", summary)
        # pr_num could be lazy lookup
        pr_number = settings.pr_num
        if pr_number:
            logger.debug("Posting comment to PR #{}", pr_number)
            post_comment(api, summary)
        else:
            logger.info("Skipping comment post since no PR number is available.")
    else:
        logger.info("No changes detected in poetry.lock")


if __name__ == "__main__":
    main()
