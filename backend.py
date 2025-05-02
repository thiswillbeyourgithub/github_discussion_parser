import click
import requests
import os
import re
from typing import List, Dict, Optional, Any
from loguru import logger

# Define constants for GitHub API
GITHUB_API_VERSION = "2022-11-28"
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

class GithubParser:
    """
    A class to interact with the GitHub API for a specific repository.
    """
    def __init__(self, repository_url: str, token: Optional[str] = None):
        """
        Initializes the GithubParser.

        Args:
            repository_url: The URL of the GitHub repository (e.g., https://github.com/owner/repo).
            token: GitHub Personal Access Token. If None, attempts to read from GITHUB_TOKEN env var.

        Raises:
            ValueError: If the repository URL is invalid or the token is not provided and not found in env vars.
        """
        self.repository_url = repository_url
        self.owner, self.repo = self._parse_repo_url(repository_url)

        if token:
            self.token = token
        else:
            self.token = os.environ.get("GITHUB_TOKEN")

        if not self.token:
            raise ValueError("GitHub token must be provided via --token argument or GITHUB_TOKEN environment variable.")

        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        self.api_repo_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}"
        self.graphql_url = GITHUB_GRAPHQL_URL

    def _parse_repo_url(self, url: str) -> tuple[str, str]:
        """
        Parses the owner and repository name from a GitHub URL.

        Args:
            url: The GitHub repository URL.

        Returns:
            A tuple containing the owner and repository name.

        Raises:
            ValueError: If the URL format is invalid or cannot be parsed.
        """
        # Regex to match GitHub repository URLs, capturing owner and repo, ignoring subsequent paths
        # It handles optional protocol, www., .git suffix, and trailing slashes or paths.
        match = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:(?:\.git)?(?:/.*)?|/?)$", url)
        if not match:
            raise ValueError(f"Could not parse owner and repository from URL: {url}")
        owner, repo = match.groups()
        # Clean up potential trailing '.git' if captured by mistake (though the regex tries to avoid it)
        if repo.endswith('.git'):
            repo = repo[:-4]
        return owner, repo

    def get_contributors(self) -> List[Dict[str, Any]]:
        """
        Fetches the list of contributors for the repository.

        Returns:
            A list of dictionaries, where each dictionary represents a contributor.

        Raises:
            requests.exceptions.RequestException: If the API request fails.
            ValueError: If the API response indicates an error (e.g., bad credentials, repo not found).
        """
        contributors_url = f"{self.api_repo_url}/contributors"
        try:
            response = requests.get(contributors_url, headers=self.headers)
            response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            # Provide more context for common errors
            if response.status_code == 401:
                raise ValueError("Bad credentials. Please check your GitHub token.") from http_err
            elif response.status_code == 403:
                 # Could be rate limiting or insufficient permissions
                 error_details = response.json().get('message', 'Forbidden')
                 raise ValueError(f"Forbidden: {error_details}. Check token permissions or rate limits.") from http_err
            elif response.status_code == 404:
                raise ValueError(f"Repository not found at {self.repository_url}. Check the URL and token permissions.") from http_err
            else:
                raise ValueError(f"HTTP error occurred: {http_err}") from http_err
        except requests.exceptions.RequestException as req_err:
            raise requests.exceptions.RequestException(f"An error occurred during the API request: {req_err}") from req_err

    def get_discussions(
        self,
        query_text: Optional[str] = None,
        in_title: bool = False,
        in_body: bool = False,
        in_comments: bool = False,
        author: Optional[str] = None,
        # commenter: Optional[str] = None, # Not directly supported via GraphQL search query string
        # answered_by: Optional[str] = None, # Not directly supported via GraphQL search query string
        involves: Optional[str] = None,
        is_open: Optional[bool] = None,
        is_answered: Optional[bool] = None,
        is_locked: Optional[bool] = None,
        category: Optional[str] = None,
        label: Optional[str] = None,
        created_after: Optional[str] = None, # YYYY-MM-DD
        created_before: Optional[str] = None, # YYYY-MM-DD
        # updated_after: Optional[str] = None, # YYYY-MM-DD - Replaced by the parameter below
        updated_before: Optional[str] = None, # YYYY-MM-DD
        # min_comments: Optional[int] = None, # Not directly supported via GraphQL search query string
        # max_comments: Optional[int] = None, # Not directly supported via GraphQL search query string
        updated_after: Optional[str] = None, # Renamed from updated_after for clarity, YYYY-MM-DD
        per_page: int = 30,
        after_cursor: Optional[str] = None # Changed 'page' to 'after_cursor' for GraphQL
    ) -> Dict[str, Any]:
        """
        Searches for discussions within the repository based on specified criteria using the GraphQL API.

        Args:
            query_text: Text to search for in title, body, or comments (unless 'in_' flags are used).
            in_title: If True, search query_text only in the title.
            in_body: If True, search query_text only in the body.
            in_comments: If True, search query_text only in the comments.
            author: Filter by the username of the discussion author.
            involves: Filter by a user involved (author, mentioned, commenter).
            is_open: Filter by open (True) or closed (False) state.
            is_answered: Filter by answered (True) or unanswered (False) state.
            is_locked: Filter by locked (True) or unlocked (False) state.
            category: Filter by discussion category name.
            label: Filter by label name.
            created_after: Filter discussions created on or after this date (YYYY-MM-DD).
            created_before: Filter discussions created on or before this date (YYYY-MM-DD).
            updated_before: Filter discussions updated on or before this date (YYYY-MM-DD).
            updated_after: Filter discussions updated on or after this date (YYYY-MM-DD).
            per_page: Number of results per page (max 100).
            after_cursor: The cursor for pagination (use None for the first page).

        Returns:
            A dictionary containing the search results from the GitHub GraphQL API.
            Structure includes keys like 'data', potentially 'errors'.
            'data' -> 'search' -> 'nodes', 'discussionCount', 'pageInfo'

        Raises:
            requests.exceptions.RequestException: If the API request fails.
            ValueError: If the API response indicates an error (e.g., bad credentials, query errors).
        """
        # Construct the search query string for GraphQL
        query_parts = [f"repo:{self.owner}/{self.repo}", "is:discussion"] # Base query scope

        if query_text:
            in_qualifiers = []
            if in_title: in_qualifiers.append("title")
            if in_body: in_qualifiers.append("body")
            if in_comments: in_qualifiers.append("comments")

            # Quote query_text if it contains spaces
            query_text_quoted = f'"{query_text}"' if ' ' in query_text else query_text

            if in_qualifiers:
                query_parts.append(f'{query_text_quoted} in:{",".join(in_qualifiers)}')
            else:
                # Default search in title, body, and comments if no specific 'in:' is chosen
                query_parts.append(query_text_quoted)

        # Add qualifiers based on arguments
        if author: query_parts.append(f"author:{author}")
        # if commenter: query_parts.append(f"commenter:{commenter}") # Not directly supported
        # if answered_by: query_parts.append(f"answered-by:{answered_by}") # Not directly supported
        if involves: query_parts.append(f"involves:{involves}")
        # Quote category/label names if they contain spaces
        if category: query_parts.append(f'category:"{category}"' if ' ' in category else f'category:{category}')
        if label: query_parts.append(f'label:"{label}"' if ' ' in label else f'label:{label}')

        # Boolean qualifiers
        if is_open is not None: query_parts.append("is:open" if is_open else "is:closed")
        if is_answered is not None: query_parts.append("is:answered" if is_answered else "is:unanswered")
        if is_locked is not None: query_parts.append("is:locked" if is_locked else "is:unlocked")

        # Date qualifiers (YYYY-MM-DD format expected)
        if created_after and created_before: query_parts.append(f"created:{created_after}..{created_before}")
        elif created_after: query_parts.append(f"created:>={created_after}")
        elif created_before: query_parts.append(f"created:<={created_before}")

        # Handle updated date range or single date
        if updated_after and updated_before:
            query_parts.append(f"updated:{updated_after}..{updated_before}")
        elif updated_after:
            # Validate YYYY-MM-DD format (basic check)
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", updated_after):
                raise ValueError("updated_after date must be in YYYY-MM-DD format.")
            query_parts.append(f"updated:>={updated_after}")
        elif updated_before:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", updated_before):
                raise ValueError("updated_before date must be in YYYY-MM-DD format.")
            query_parts.append(f"updated:<={updated_before}")


        # Comment count qualifiers (Not directly supported in basic GraphQL search query string)
        # if min_comments is not None and max_comments is not None:
        #      query_parts.append(f"comments:{min_comments}..{max_comments}")
        # elif min_comments is not None:
        #      query_parts.append(f"comments:>={min_comments}")
        # elif max_comments is not None:
        #      query_parts.append(f"comments:<={max_comments}")

        # Construct the final query string
        search_query_string = " ".join(query_parts)

        # Define the GraphQL query
        # We fetch basic discussion fields. Add more fields from the Discussion object if needed.
        graphql_query = """
        query SearchDiscussions($query: String!, $first: Int!, $after: String) {
          search(query: $query, type: DISCUSSION, first: $first, after: $after) {
            discussionCount
            pageInfo {
              endCursor
              hasNextPage
              hasPreviousPage
              startCursor
            }
            nodes {
              ... on Discussion {
                id
                number
                url
                title
                author {
                  login
                }
                createdAt
                updatedAt
                category {
                  name
                }
                answer {
                  id
                }
                bodyText
                comments(first: 1) { # Just to get comment count easily if needed later, or check existence
                    totalCount
                }
                labels(first: 10) {
                  nodes {
                    name
                  }
                }
                locked
                repository {
                  nameWithOwner
                }
              }
            }
            edges {
                cursor # Often same as node's endCursor for search results
                node {
                   ... on Discussion {
                       id # Example: Redundant if using nodes, but shows edge structure
                   }
                }
            }
          }
        }
        """

        variables = {
            "query": search_query_string,
            "first": min(per_page, 100), # Ensure per_page doesn't exceed max
            "after": after_cursor
        }

        payload = {
            "query": graphql_query,
            "variables": variables
        }

        logger.debug(f"Sending GraphQL Request: Query='{search_query_string}', PerPage={variables['first']}, After={variables['after']}")

        try:
            response = requests.post(self.graphql_url, headers=self.headers, json=payload)
            response.raise_for_status()
            result_json = response.json()

            # Check for GraphQL errors in the response body
            if "errors" in result_json:
                raise ValueError(f"GraphQL API errors: {result_json['errors']}")

            # Check if 'data' key exists before returning
            if "data" not in result_json:
                 raise ValueError(f"GraphQL response missing 'data' key. Response: {result_json}")

            return result_json # Return the full GraphQL response structure

        except requests.exceptions.HTTPError as http_err:
            # Attempt to get more details from the response body if possible
            error_details = ""
            try:
                error_details = response.json()
            except requests.exceptions.JSONDecodeError:
                error_details = response.text

            if response.status_code == 401:
                raise ValueError(f"Bad credentials. Please check your GitHub token. Details: {error_details}") from http_err
            elif response.status_code == 403:
                 raise ValueError(f"Forbidden. Check token permissions or rate limits. Details: {error_details}") from http_err
            # GraphQL often returns 200 OK even with query errors, handled above.
            # Other HTTP errors are still possible.
            else:
                raise ValueError(f"HTTP error occurred: {http_err} (Status code: {response.status_code}). Response: {error_details}") from http_err
        except requests.exceptions.RequestException as req_err:
            raise requests.exceptions.RequestException(f"An error occurred during the API request: {req_err}") from req_err
        except ValueError as val_err: # Catch GraphQL errors raised above
            raise val_err # Re-raise the specific ValueError

    def get_discussion_details(self, discussion_number: int) -> Dict[str, Any]:
        """
        Fetches detailed information for a single discussion, including its comments and replies (paginated).

        Args:
            discussion_number: The number of the discussion to fetch.

        Returns:
            A dictionary containing the detailed discussion data from the GitHub GraphQL API.

        Raises:
            requests.exceptions.RequestException: If the API request fails.
            ValueError: If the API response indicates an error or missing data.
        """
        # Fetch first 100 comments and first 100 replies per comment.
        # Add more fields as needed.
        graphql_query = """
        query GetDiscussionDetails($owner: String!, $repo: String!, $number: Int!, $commentsFirst: Int!, $repliesFirst: Int!) {
          repository(owner: $owner, name: $repo) {
            discussion(number: $number) {
              id
              number
              url
              title
              author { login }
              createdAt
              updatedAt
              category { name }
              isAnswered: answer { id } # Simplified check if answer exists
              bodyText
              locked
              comments(first: $commentsFirst) {
                totalCount
                pageInfo { endCursor hasNextPage }
                nodes {
                  id
                  author { login }
                  createdAt
                  updatedAt
                  bodyText
                  isMinimized
                  minimizedReason
                  replies(first: $repliesFirst) {
                    totalCount
                    pageInfo { endCursor hasNextPage }
                    nodes {
                      id
                      author { login }
                      createdAt
                      updatedAt
                      bodyText
                      isMinimized
                      minimizedReason
                      # Cannot fetch replies of replies easily without deeper nesting/more queries
                    }
                  }
                }
              }
              labels(first: 10) { nodes { name } }
              # Add other discussion fields if needed
            }
          }
        }
        """

        variables = {
            "owner": self.owner,
            "repo": self.repo,
            "number": discussion_number,
            "commentsFirst": 100, # Max per page
            "repliesFirst": 100   # Max per page
        }

        payload = {
            "query": graphql_query,
            "variables": variables
        }

        logger.debug(f"Sending GraphQL Request: GetDiscussionDetails for #{discussion_number}")

        try:
            response = requests.post(self.graphql_url, headers=self.headers, json=payload)
            response.raise_for_status()
            result_json = response.json()

            if "errors" in result_json:
                raise ValueError(f"GraphQL API errors fetching discussion #{discussion_number}: {result_json['errors']}")

            if "data" not in result_json or not result_json["data"].get("repository") or not result_json["data"]["repository"].get("discussion"):
                 raise ValueError(f"GraphQL response missing expected data for discussion #{discussion_number}. Response: {result_json}")

            # Return the specific discussion object from the response
            return result_json["data"]["repository"]["discussion"]

        except requests.exceptions.HTTPError as http_err:
            error_details = ""
            try: error_details = response.json()
            except requests.exceptions.JSONDecodeError: error_details = response.text
            if response.status_code == 401: raise ValueError(f"Bad credentials. Check token. Details: {error_details}") from http_err
            elif response.status_code == 403: raise ValueError(f"Forbidden. Check token permissions/rate limits. Details: {error_details}") from http_err
            else: raise ValueError(f"HTTP error fetching discussion #{discussion_number}: {http_err}. Response: {error_details}") from http_err
        except requests.exceptions.RequestException as req_err:
            raise requests.exceptions.RequestException(f"Network error fetching discussion #{discussion_number}: {req_err}") from req_err
        except ValueError as val_err:
            raise val_err # Re-raise specific ValueErrors


@click.command()
@click.option('-r', '--repository', required=True, help='GitHub repository URL (e.g., https://github.com/owner/repo).')
@click.option('-t', '--token', default=None, help='GitHub Personal Access Token. Reads from GITHUB_TOKEN env var if not provided.')
def main(repository: str, token: Optional[str]):
    """
    A CLI tool to fetch information from a GitHub repository.
    Currently fetches and prints the list of contributors.
    """
    try:
        parser = GithubParser(repository_url=repository, token=token)
        contributors = parser.get_contributors()

        logger.info(f"Contributors for {parser.owner}/{parser.repo}:")
        if contributors:
            for contributor in contributors:
                logger.info(f"- {contributor.get('login', 'N/A')} (Contributions: {contributor.get('contributions', 'N/A')})")
        else:
            logger.info("No contributors found.")

    except (ValueError, requests.exceptions.RequestException) as e:
        logger.exception(f"Failed to fetch contributors: {e}")
        # Consider exiting with a non-zero status code for errors
        import sys
        sys.exit(1)

if __name__ == "__main__":
    main()
