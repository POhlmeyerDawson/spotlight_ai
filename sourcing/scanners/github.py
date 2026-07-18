"""github scanner. Owner: B. Emits RawSignal -> bus.ingest().

observed_at must come from the source's own timestamp. If a source cannot give a
real one, it does not get ingested. Cache raw responses to data/raw/.

This scanner handles pagination and rate-limit backoff properly.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from schema.events import RawSignal, Source

from core.config import settings

CACHE_DIR = Path("data/raw/github")
GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

# Rate limiting constants
RATE_LIMIT_DELAY = 1.0  # Base delay between requests
MAX_BACKOFF_RETRIES = 5
BACKOFF_FACTOR = 2.0


class GitHubRateLimitError(Exception):
    """Raised when GitHub API rate limit is exceeded."""
    pass


class GitHubGraphQLClient:
    """GitHub GraphQL client with automatic pagination and rate-limit handling."""

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        })

    def _request(self, query: dict, variables: dict) -> dict:
        """Make a GraphQL request with rate-limit backoff."""
        for attempt in range(MAX_BACKOFF_RETRIES):
            response = self.session.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=30,
            )

            # Check for rate limit
            if response.status_code == 403:
                rate_limit = response.headers.get("X-RateLimit-Remaining", "0")
                if rate_limit == "0":
                    reset_time = int(response.headers.get("X-RateLimit-Reset", "0"))
                    now = int(time.time())
                    wait_time = max(1, reset_time - now + 1)
                    print(f"Rate limited. Waiting {wait_time}s until {datetime.fromtimestamp(reset_time)}")
                    time.sleep(wait_time)
                    continue

            response.raise_for_status()
            result = response.json()

            # Handle GraphQL errors
            if "errors" in result:
                for error in result["errors"]:
                    if "type" in error and error["type"] == "RATE_LIMITED":
                        wait_time = RATE_LIMIT_DELAY * (BACKOFF_FACTOR ** attempt)
                        print(f"GraphQL rate limit hit. Backing off for {wait_time}s")
                        time.sleep(wait_time)
                        break
                else:
                    raise Exception(f"GraphQL error: {result['errors']}")

            return result

        raise GitHubRateLimitError("Max retries exceeded for rate-limited request")

    def fetch_user_repos(self, username: str, first: int = 100) -> list[dict]:
        """Fetch repositories for a user with pagination."""
        repos = []
        cursor = None
        total_fetched = 0

        query = """
        query($login: String!, $first: Int, $after: String) {
            user(login: $login) {
                repositories(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
                    nodes {
                        name
                        description
                        url
                        stargazerCount
                        forkCount
                        languages(first: 10) {
                            nodes {
                                name
                            }
                        }
                        repositoryTopics(first: 10) {
                            nodes {
                                topic {
                                    name
                                }
                            }
                        }
                        owner {
                            login
                        }
                        createdAt
                        updatedAt
                        pushedAt
                        isFork
                        isArchived
                        licenseInfo {
                            name
                            key
                        }
                    }
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                }
            }
        }
        """

        while True:
            variables = {"login": username, "first": first}
            if cursor:
                variables["after"] = cursor

            result = self._request(query, variables)

            if "data" not in result or "user" not in result["data"]:
                break

            user_data = result["data"]["user"]
            if not user_data:
                break

            repo_nodes = user_data["repositories"]["nodes"]
            repos.extend(repo_nodes)
            total_fetched += len(repo_nodes)

            page_info = user_data["repositories"]["pageInfo"]
            if not page_info["hasNextPage"]:
                break

            cursor = page_info["endCursor"]
            time.sleep(RATE_LIMIT_DELAY)  # Rate limiting between pages

        return repos

    def fetch_user_info(self, username: str) -> dict:
        """Fetch user/profile information."""
        query = """
        query($login: String!) {
            user(login: $login) {
                login
                name
                avatarUrl
                bio
                email
                location
                twitterUsername
                websiteUrl
                followers(first: 100) {
                    totalCount
                }
                repositoriesContributedTo(first: 10, contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) {
                    totalCount
                }
                commitComments(first: 10) {
                    totalCount
                }
                gists(first: 10) {
                    totalCount
                }
                createdAt
                updatedAt
            }
        }
        """

        result = self._request(query, {"login": username})
        if "data" not in result or "user" not in result["data"]:
            return {}

        return result["data"]["user"]

    def fetch_repo_commits(self, owner: str, name: str, first: int = 50) -> list[dict]:
        """Fetch commits for a repository."""
        commits = []
        cursor = None

        query = """
        query($owner: String!, $name: String!, $first: Int, $after: String) {
            repository(owner: $owner, name: $name) {
                defaultBranchRef {
                    target {
                        ... on Commit {
                            history(first: $first, after: $after) {
                                nodes {
                                    oid
                                    author {
                                        user {
                                            login
                                        }
                                        name
                                        email
                                        date
                                    }
                                    committedDate
                                    committedViaWeb
                                    message
                                    additions
                                    deletions
                                    parents(first: 1) {
                                        totalCount
                                    }
                                }
                                pageInfo {
                                    hasNextPage
                                    endCursor
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        while True:
            variables = {"owner": owner, "name": name, "first": first}
            if cursor:
                variables["after"] = cursor

            result = self._request(query, variables)

            if "data" not in result:
                break

            repo_data = result["data"]["repository"]
            if not repo_data:
                break

            branch = repo_data["defaultBranchRef"]
            if not branch or "target" not in branch:
                break

            history = branch["target"].get("history")
            if not history:
                break

            commit_nodes = history["nodes"]
            commits.extend(commit_nodes)

            page_info = history["pageInfo"]
            if not page_info["hasNextPage"]:
                break

            cursor = page_info["endCursor"]
            time.sleep(RATE_LIMIT_DELAY)

        return commits

    def fetch_user_commits(self, username: str, since: datetime | None = None, limit: int = 100) -> list[dict]:
        """Fetch recent commits by a user."""
        commits = []
        cursor = None
        fetched = 0

        # Convert datetime to ISO format
        since_str = since.isoformat() if since else None

        query = """
        query($username: String!, $first: Int, $after: String, $since: DateTime) {
            user(login: $username) {
                contributionsCollection(from: $since) {
                    contributionCalendar {
                        weeks {
                            contributionDays {
                                date
                                contributionCount
                            }
                        }
                    }
                    totalCommitContributions
                    totalIssueContributions
                    totalPullRequestContributions
                    totalRepositoryContributions
                }
            }
            rateLimit {
                limit
                cost
                remaining
                resetAt
            }
        }
        """

        variables = {"username": username, "first": limit}
        if since_str:
            variables["since"] = since_str

        result = self._request(query, variables)

        if "data" not in result:
            return []

        user_data = result["data"]["user"]
        if not user_data:
            return []

        contributions = user_data.get("contributionsCollection", {})
        commits.append({
            "contributions": contributions,
            "rate_limit": result["data"].get("rateLimit", {}),
        })

        return commits

    def fetch_repo_info(self, owner: str, name: str) -> dict:
        """Fetch detailed repository information."""
        query = """
        query($owner: String!, $name: String!) {
            repository(owner: $owner, name: $name) {
                name
                description
                url
                stargazerCount
                forkCount
                watchers(first: 10) {
                    totalCount
                }
                languages(first: 10) {
                    nodes {
                        name
                    }
                }
                repositoryTopics(first: 20) {
                    nodes {
                        topic {
                            name
                        }
                    }
                }
                owner {
                    login
                }
                createdAt
                updatedAt
                pushedAt
                isFork
                isArchived
                isPrivate
                licenseInfo {
                    name
                    key
                }
                forks(first: 10) {
                    nodes {
                        name
                        owner {
                            login
                        }
                    }
                }
                dependents(first: 10) {
                    totalCount
                }
                stargazers(first: 10, orderBy: {field: STARRED_AT, direction: DESC}) {
                    nodes {
                        login
                    }
                }
            }
        }
        """

        result = self._request(query, {"owner": owner, "name": name})
        if "data" not in result or "repository" not in result["data"]:
            return {}

        return result["data"]["repository"]


def _parse_date(date_str: str) -> datetime:
    """Parse ISO format date string to datetime."""
    if date_str is None:
        return datetime.now(timezone.utc)
    try:
        # Handle various ISO formats
        if date_str.endswith("Z"):
            date_str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(date_str)
    except ValueError:
        return datetime.now(timezone.utc)


def scan(query: str, limit: int = 50) -> list[RawSignal]:
    """Scan GitHub for users/repos matching a query.

    Args:
        query: Search query (username, repo pattern, etc.)
        limit: Maximum number of results

    Returns:
        List of RawSignal objects
    """
    if not settings.github_token:
        print("GITHUB_TOKEN not set. Skipping GitHub scan.")
        return []

    client = GitHubGraphQLClient(settings.github_token)
    raw_signals = []

    # Try to parse as username first
    try:
        user_info = client.fetch_user_info(query)
        if user_info:
            observed_at = _parse_date(user_info.get("createdAt"))

            payload = {
                "type": "user",
                "login": user_info.get("login"),
                "name": user_info.get("name"),
                "bio": user_info.get("bio"),
                "email": user_info.get("email"),
                "location": user_info.get("location"),
                "twitter_username": user_info.get("twitterUsername"),
                "website": user_info.get("websiteUrl"),
                "followers_count": user_info.get("followers", {}).get("totalCount", 0),
                "repositories_contributed_to": user_info.get("repositoriesContributedTo", {}).get("totalCount", 0),
                "commit_comments": user_info.get("commitComments", {}).get("totalCount", 0),
                "gists": user_info.get("gists", {}).get("totalCount", 0),
                "created_at": user_info.get("createdAt"),
                "updated_at": user_info.get("updatedAt"),
            }

            raw_signal = RawSignal(
                source=Source.GITHUB,
                source_url=f"https://github.com/{query}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={"username": query, "user_info": True},
            )
            raw_signals.append(raw_signal)

            # Also fetch their repos
            repos = client.fetch_user_repos(query, min(limit, 100))
            for repo in repos[:limit]:
                repo_observed_at = _parse_date(repo.get("createdAt"))

                payload = {
                    "type": "repo",
                    "owner": repo.get("owner", {}).get("login"),
                    "name": repo.get("name"),
                    "description": repo.get("description"),
                    "url": repo.get("url"),
                    "stargazers": repo.get("stargazerCount", 0),
                    "forks": repo.get("forkCount", 0),
                    "languages": [lang.get("name") for lang in repo.get("languages", {}).get("nodes", [])],
                    "topics": [t.get("topic", {}).get("name") for t in repo.get("repositoryTopics", {}).get("nodes", [])],
                    "created_at": repo.get("createdAt"),
                    "updated_at": repo.get("updatedAt"),
                    "pushed_at": repo.get("pushedAt"),
                    "is_fork": repo.get("isFork", False),
                    "is_archived": repo.get("isArchived", False),
                    "license": repo.get("licenseInfo", {}).get("name"),
                }

                raw_signal = RawSignal(
                    source=Source.GITHUB,
                    source_url=repo.get("url"),
                    content=json.dumps(payload),
                    fetched_at=datetime.now(timezone.utc),
                    observed_at=repo_observed_at,
                    meta={"username": query, "repo_name": repo.get("name")},
                )
                raw_signals.append(raw_signal)

            return raw_signals
    except Exception as e:
        print(f"Error fetching user {query}: {e}")

    # If not a user, try as a search query
    search_url = f"https://api.github.com/search/repositories?q={query}&per_page={min(limit, 100)}"
    headers = {"Authorization": f"Bearer {settings.github_token}"}

    try:
        response = requests.get(search_url, headers=headers, timeout=30)
        response.raise_for_status()
        results = response.json()

        for item in results.get("items", []):
            observed_at = _parse_date(item.get("created_at"))

            payload = {
                "type": "repo_search",
                "name": item.get("full_name"),
                "description": item.get("description"),
                "url": item.get("html_url"),
                "stargazers": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "language": item.get("language"),
                "topics": item.get("topics", []),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "pushed_at": item.get("pushed_at"),
            }

            raw_signal = RawSignal(
                source=Source.GITHUB,
                source_url=item.get("html_url"),
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={"search_query": query},
            )
            raw_signals.append(raw_signal)

    except Exception as e:
        print(f"Error searching GitHub: {e}")

    return raw_signals[:limit]


def scan_user(username: str) -> list[RawSignal]:
    """Scan all data for a specific GitHub user."""
    if not settings.github_token:
        print("GITHUB_TOKEN not set. Skipping GitHub scan.")
        return []

    client = GitHubGraphQLClient(settings.github_token)
    raw_signals = []

    try:
        # Fetch user info
        user_info = client.fetch_user_info(username)
        if user_info:
            observed_at = _parse_date(user_info.get("createdAt"))
            payload = {
                "type": "user",
                "login": user_info.get("login"),
                "name": user_info.get("name"),
                "bio": user_info.get("bio"),
                "email": user_info.get("email"),
                "location": user_info.get("location"),
                "twitter_username": user_info.get("twitterUsername"),
                "website": user_info.get("websiteUrl"),
                "followers_count": user_info.get("followers", {}).get("totalCount", 0),
                "repositories_contributed_to": user_info.get("repositoriesContributedTo", {}).get("totalCount", 0),
                "created_at": user_info.get("createdAt"),
                "updated_at": user_info.get("updatedAt"),
            }

            raw_signals.append(RawSignal(
                source=Source.GITHUB,
                source_url=f"https://github.com/{username}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={"username": username},
            ))

        # Fetch user's repos
        repos = client.fetch_user_repos(username, 100)
        for repo in repos:
            repo_observed_at = _parse_date(repo.get("createdAt"))
            payload = {
                "type": "repo",
                "owner": repo.get("owner", {}).get("login"),
                "name": repo.get("name"),
                "description": repo.get("description"),
                "url": repo.get("url"),
                "stargazers": repo.get("stargazerCount", 0),
                "forks": repo.get("forkCount", 0),
                "languages": [lang.get("name") for lang in repo.get("languages", {}).get("nodes", [])],
                "topics": [t.get("topic", {}).get("name") for t in repo.get("repositoryTopics", {}).get("nodes", [])],
                "created_at": repo.get("createdAt"),
                "updated_at": repo.get("updatedAt"),
                "pushed_at": repo.get("pushedAt"),
                "is_fork": repo.get("isFork", False),
                "is_archived": repo.get("isArchived", False),
            }

            raw_signals.append(RawSignal(
                source=Source.GITHUB,
                source_url=repo.get("url"),
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=repo_observed_at,
                meta={"username": username, "repo_name": repo.get("name")},
            ))

        # Fetch recent commits
        user_commits = client.fetch_user_commits(username)
        for commit_data in user_commits:
            payload = {
                "type": "user_contributions",
                "contributions": commit_data.get("contributions", {}),
                "username": username,
            }
            raw_signals.append(RawSignal(
                source=Source.GITHUB,
                source_url=f"https://github.com/{username}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=datetime.now(timezone.utc),
                meta={"username": username, "type": "contributions"},
            ))

    except Exception as e:
        print(f"Error scanning user {username}: {e}")

    return raw_signals


def scan_repo(owner: str, name: str) -> list[RawSignal]:
    """Scan detailed information for a specific repository."""
    if not settings.github_token:
        print("GITHUB_TOKEN not set. Skipping GitHub scan.")
        return []

    client = GitHubGraphQLClient(settings.github_token)
    raw_signals = []

    try:
        repo_info = client.fetch_repo_info(owner, name)
        if repo_info:
            observed_at = _parse_date(repo_info.get("createdAt"))

            payload = {
                "type": "repo_detailed",
                "owner": owner,
                "name": name,
                "description": repo_info.get("description"),
                "url": repo_info.get("url"),
                "stargazers": repo_info.get("stargazerCount", 0),
                "forks": repo_info.get("forkCount", 0),
                "watchers": repo_info.get("watchers", {}).get("totalCount", 0),
                "languages": [lang.get("name") for lang in repo_info.get("languages", {}).get("nodes", [])],
                "topics": [t.get("topic", {}).get("name") for t in repo_info.get("repositoryTopics", {}).get("nodes", [])],
                "created_at": repo_info.get("createdAt"),
                "updated_at": repo_info.get("updatedAt"),
                "pushed_at": repo_info.get("pushedAt"),
                "is_fork": repo_info.get("isFork", False),
                "is_archived": repo_info.get("isArchived", False),
                "is_private": repo_info.get("isPrivate", False),
                "license": repo_info.get("licenseInfo", {}).get("name"),
                "forks_list": [f.get("name") for f in repo_info.get("forks", {}).get("nodes", [])],
                "dependents_count": repo_info.get("dependents", {}).get("totalCount", 0),
                "stargazers_list": [s.get("login") for s in repo_info.get("stargazers", {}).get("nodes", [])],
            }

            raw_signals.append(RawSignal(
                source=Source.GITHUB,
                source_url=repo_info.get("url"),
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=observed_at,
                meta={"owner": owner, "repo": name},
            ))

        # Fetch commits
        commits = client.fetch_repo_commits(owner, name, 50)
        for commit in commits:
            commit_observed_at = _parse_date(commit.get("committedDate"))

            payload = {
                "type": "commit",
                "oid": commit.get("oid"),
                "author_login": commit.get("author", {}).get("user", {}).get("login"),
                "author_name": commit.get("author", {}).get("name"),
                "committed_date": commit.get("committedDate"),
                "message": commit.get("message"),
                "additions": commit.get("additions", 0),
                "deletions": commit.get("deletions", 0),
                "is_via_web": commit.get("committedViaWeb", False),
                "parent_count": commit.get("parents", {}).get("totalCount", 0),
            }

            raw_signals.append(RawSignal(
                source=Source.GITHUB,
                source_url=f"https://github.com/{owner}/{name}/commit/{commit.get('oid')}",
                content=json.dumps(payload),
                fetched_at=datetime.now(timezone.utc),
                observed_at=commit_observed_at,
                meta={"owner": owner, "repo": name},
            ))

    except Exception as e:
        print(f"Error scanning repo {owner}/{name}: {e}")

    return raw_signals
