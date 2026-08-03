from enum import Enum
from urllib.parse import urlparse

import requests
from github import Auth, Github
from github.GithubException import GithubException
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.Repository import Repository
from loguru import logger
from requests import Response

from diff_poetry_lock.settings import Settings
from diff_poetry_lock.utils import get_nested

MAGIC_COMMENT_IDENTIFIER = "<!-- posted by target/diff-poetry-lock -->\n\n"


class RepoFileRetrievalError(BaseException):
    def __init__(self, repo: str, branch: str) -> None:
        msg = f"Error accessing a file in repo [{repo}] on branch [{branch}]"
        super().__init__(msg)


class GithubApi:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.session()
        self.github = Github(auth=Auth.Token(self.s.token.get_secret_value()), base_url=self.s.api_url.rstrip("/"), per_page=100)
        self._repo: Repository | None = None
        self.requester = self.github.requester
        self._ref_hash_cache: dict[str, str] = {}

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self._repo = self.github.get_repo(self.s.repository)
        return self._repo

    def build_issue_comment_url(self, comment_id: int) -> str:
        return f"/repos/{self.s.repository}/issues/comments/{comment_id}"

    def build_issue_url(self) -> str:
        return f"/repos/{self.s.repository}/issues/{self.s.pr_num}"

    def post_comment(self, comment: str) -> None:
        if not comment:
            logger.info("No changes to lockfile detected")
            return

        if not self.s.pr_num:
            logger.warning("No PR number available; skipping comment post")
            return

        logger.debug("Posting comment to PR #{}", self.s.pr_num)

        issue = Issue(
            requester=self.requester,
            url=self.build_issue_url(),
        )

        issue.create_comment(body=f"{MAGIC_COMMENT_IDENTIFIER}{comment}")

    def update_comment(self, comment_id: int, comment: str) -> None:
        logger.debug("Updating comment {}", comment_id)
        if not self.s.pr_num:
            logger.warning("No PR number available; skipping comment update")
            return

        issue_comment = IssueComment(
            requester=self.requester,
            url=self.build_issue_comment_url(comment_id),
        )

        issue_comment.edit(body=f"{MAGIC_COMMENT_IDENTIFIER}{comment}")

    def list_comments(self) -> list[IssueComment]:
        if not self.s.pr_num:
            logger.warning("No PR number available; returning empty comment list")
            return []

        logger.debug("Fetching comments for PR #{}", self.s.pr_num)

        issue = Issue(
            requester=self.requester,
            url=self.build_issue_url(),
        )

        all_comments = issue.get_comments()

        logger.debug("Found {} comments", all_comments.totalCount)

        def is_diff_comment(comment: IssueComment) -> bool:
            return comment.body.startswith(MAGIC_COMMENT_IDENTIFIER)

        return [c for c in all_comments if is_diff_comment(c)]

    def get_file(self, ref: str) -> Response:
        logger.debug("Fetching {} from ref {}", self.s.lockfile_path, ref)

        r = self.session.get(
            f"{self.s.api_url}/repos/{self.s.repository}/contents/{self.s.lockfile_path}",
            params={"ref": ref},
            headers=self.Headers.RAW.headers(self.s.token.get_secret_value()),
            timeout=10,
            stream=True,
        )
        logger.debug("Response status: {}", r.status_code)

        if r.status_code == 404:
            raise FileNotFoundError(self.s.lockfile_path) from RepoFileRetrievalError(self.s.repository, ref)
        r.raise_for_status()
        return r

    def resolve_commit_hashes(self) -> tuple[str, str]:
        cached_head_hash = self._ref_hash_cache.get(self.s.head_ref)
        cached_base_hash = self._ref_hash_cache.get(self.s.base_ref)
        if cached_head_hash and cached_base_hash:
            logger.debug("Using cached commit hashes for head_ref {} and base_ref {}", self.s.head_ref, self.s.base_ref)
            return cached_head_hash, cached_base_hash

        if not self.s.pr_num:
            logger.warning("No PR number available; skipping commit hash resolution")
            return self.s.head_ref, self.s.base_ref

        owner, repo_name = self.s.repository.split("/", maxsplit=1)
        query = (
            "query($owner:String!, $name:String!, $number:Int!){"
            " repository(owner:$owner, name:$name){"
            "  pullRequest(number:$number) { headRefOid baseRefOid }"
            " }"
            "}"
        )
        variables = {
            "owner": owner,
            "name": repo_name,
            "number": self.s.pr_num,
        }

        try:
            _, response_json = self.requester.graphql_query(query, variables)

            repo_data = response_json.get("data", {}).get("repository", {})

            resolved_head_hash = str(get_nested(repo_data, ("pullRequest", "headRefOid")) or "").strip()

            resolved_base_hash = str(get_nested(repo_data, ("pullRequest", "baseRefOid")) or "").strip()

            if resolved_head_hash:
                self._ref_hash_cache[self.s.head_ref] = resolved_head_hash
            if resolved_base_hash:
                self._ref_hash_cache[self.s.base_ref] = resolved_base_hash

        except (GithubException, ValueError, TypeError):
            logger.exception("Failed to resolve commit hashes via GraphQL")

        resolved_head_hash = self._ref_hash_cache.get(self.s.head_ref, self.s.head_ref)
        resolved_base_hash = self._ref_hash_cache.get(self.s.base_ref, self.s.base_ref)
        if resolved_head_hash == self.s.head_ref or resolved_base_hash == self.s.base_ref:
            logger.warning("Could not resolve one or more commit hashes, falling back to provided refs")
        return resolved_head_hash, resolved_base_hash

    def graphql_url(self) -> str:
        parsed = urlparse(self.s.api_url)
        if parsed.path.endswith("/api/v3"):
            graphql_path = f"{parsed.path.removesuffix('/api/v3')}/api/graphql"
            return f"{parsed.scheme}://{parsed.netloc}{graphql_path}"

        return f"{self.s.api_url.rstrip('/')}/graphql"

    def delete_comment(self, comment_id: int) -> None:
        logger.debug("Deleting comment {}", comment_id)
        if not self.s.pr_num:
            logger.warning("No PR number available; skipping comment delete")
            return

        issue_comment = IssueComment(
            requester=self.requester,
            url=self.build_issue_comment_url(comment_id),
        )

        issue_comment.delete()

    class Headers(Enum):
        """Enum for github api headers."""

        JSON = "application/vnd.github+json"
        RAW = "application/vnd.github.raw"

        def headers(self, token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}", "Accept": self.value}

    def find_pr_for_branch(self, branch_ref: str) -> str:
        """Find open PR number for a given branch ref (e.g., 'refs/heads/deps-update').
        Returns PR number as string, or empty string if not found."""
        branch = branch_ref.replace("refs/heads/", "")
        logger.debug("Looking for open PR for branch {}", branch)

        org = self.s.repository.split("/")[0]
        head = f"{org}:{branch}"

        pulls = self.repo.get_pulls(state="open", head=head)

        if pulls.totalCount > 0:
            pr_num = str(next(iter(pulls)).number)
            logger.debug("Found open PR #{}", pr_num)
            return pr_num

        logger.debug("No open PR found for branch {}", branch)
        return ""

    def upsert_comment(self, existing_comment: IssueComment | None, comment: str | None) -> None:
        if existing_comment is None and comment is None:
            return

        if existing_comment is None and comment is not None:
            logger.info("Posting diff to new comment.")
            self.post_comment(comment)

        elif existing_comment is not None and comment is None:
            logger.info("Deleting existing comment.")
            self.delete_comment(existing_comment.id)

        elif existing_comment is not None and comment is not None:
            if existing_comment.body == f"{MAGIC_COMMENT_IDENTIFIER}{comment}":
                logger.debug("Content did not change, not updating existing comment.")
            else:
                logger.info("Updating existing comment.")
                self.update_comment(existing_comment.id, comment)
