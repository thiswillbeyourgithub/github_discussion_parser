import click
import os
import sys
import json
from pathlib import Path
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple # Added Tuple
import requests
import html # For escaping text in XML
from loguru import logger

# Configure Loguru to remove default handlers and add custom ones
logger.remove()
logger.add(sys.stderr, level="INFO") # Log INFO and above to stderr

from backend import GithubParser

def parse_since_to_date(since_str: str) -> str:
    """
    Parses a 'since' string into a YYYY-MM-DD date string.
    Accepts absolute dates (YYYY-MM-DD) or relative times (e.g., 1d, 5h, 2w, 1m, 1y).

    Args:
        since_str: The input string for the 'since' date/time.

    Returns:
        A date string in YYYY-MM-DD format.

    Raises:
        ValueError: If the format is invalid.
    """
    # Check for absolute date format first
    if re.match(r"^\d{4}-\d{2}-\d{2}$", since_str):
        try:
            # Validate it's a real date
            datetime.strptime(since_str, '%Y-%m-%d')
            return since_str
        except ValueError:
            raise ValueError(f"Invalid absolute date format or value: {since_str}")

    # Check for relative time format
    match = re.match(r"^(\d+)([dhwmy])$", since_str.lower())
    if not match:
        raise ValueError(f"Invalid format for --since: '{since_str}'. Use YYYY-MM-DD or relative format (e.g., 1d, 5h, 2w, 1m, 1y).")

    value = int(match.group(1))
    unit = match.group(2)

    now = datetime.now(timezone.utc) # Use timezone-aware current time
    delta = None

    if unit == 'd':
        delta = timedelta(days=value)
    elif unit == 'h':
        delta = timedelta(hours=value)
    elif unit == 'w':
        delta = timedelta(weeks=value)
    elif unit == 'm':
        # Approximate month as 30 days for simplicity
        delta = timedelta(days=value * 30)
    elif unit == 'y':
        # Approximate year as 365 days
        delta = timedelta(days=value * 365)

    if delta:
        target_date = now - delta
        return target_date.strftime('%Y-%m-%d')
    else:
        # Should not happen if regex matches, but as a safeguard
        raise ValueError(f"Unknown unit '{unit}' in relative time format: {since_str}")


def generate_llm_ready_markdown(discussion_details: Dict[str, Any]) -> str:
    """
    Generates an XML-like Markdown string representation of a discussion for LLM processing.

    Args:
        discussion_details: The dictionary containing detailed discussion data.

    Returns:
        A string formatted with XML-like tags.
    """
    # Helper to safely get nested values and escape HTML characters
    def safe_get(data: Optional[Dict], key: str, default: str = "N/A") -> str:
        val = data.get(key) if data else default
        return html.escape(str(val)) if val is not None else default

    # Helper to format body text within CDATA sections for robustness
    def format_body(text: Optional[str]) -> str:
        if text is None:
            return "<![CDATA[]]>"
        # Basic escaping for CDATA end sequence `]]>` and remove leading/trailing whitespace
        safe_text = text.strip().replace("]]>", "]]&gt;")
        # Remove internal newlines and excessive whitespace, replace with single space
        safe_text = re.sub(r'\s+', ' ', safe_text)
        return f"<![CDATA[{safe_text}]]>"

    # Extract top-level info
    title = safe_get(discussion_details, 'title')
    url = safe_get(discussion_details, 'url')
    number = safe_get(discussion_details, 'number')
    author_login = safe_get(discussion_details.get('author'), 'login')
    created_at = safe_get(discussion_details, 'createdAt')
    body_text = format_body(discussion_details.get('bodyText'))

    # Construct parts without indentation
    md_parts = [
        f'<discussion url="{url}" number="{number}" title="{title}">',
        f'<post author="{author_login}" createdAt="{created_at}">',
        f'<body>{body_text}</body>',
        '</post>'
    ]

    # Process comments
    comments_data = discussion_details.get('comments', {})
    comments_nodes = comments_data.get('nodes', [])
    total_comment_count = comments_data.get('totalCount', 0)

    md_parts.append(f'<comments totalCount="{total_comment_count}">')

    for comment in comments_nodes:
        comment_id = safe_get(comment, 'id')
        comment_author = safe_get(comment.get('author'), 'login')
        comment_created_at = safe_get(comment, 'createdAt')
        comment_body = format_body(comment.get('bodyText'))
        # is_minimized = safe_get(comment, 'isMinimized', 'false') # Removed
        # minimized_reason = safe_get(comment, 'minimizedReason', '') if comment.get('isMinimized') else '' # Removed

        md_parts.append(f'<comment id="{comment_id}" author="{comment_author}" createdAt="{comment_created_at}">') # Removed minimization attributes
        md_parts.append(f'<body>{comment_body}</body>')

        # Process replies
        replies_data = comment.get('replies', {})
        replies_nodes = replies_data.get('nodes', [])
        total_reply_count = replies_data.get('totalCount', 0)

        md_parts.append(f'<replies totalCount="{total_reply_count}">')
        for reply in replies_nodes:
            reply_id = safe_get(reply, 'id')
            reply_author = safe_get(reply.get('author'), 'login')
            reply_created_at = safe_get(reply, 'createdAt')
            reply_body = format_body(reply.get('bodyText'))
            reply_is_minimized = safe_get(reply, 'isMinimized', 'false')
            reply_minimized_reason = safe_get(reply, 'minimizedReason', '') if reply.get('isMinimized') else ''

            md_parts.append(f'<reply id="{reply_id}" author="{reply_author}" createdAt="{reply_created_at}" isMinimized="{reply_is_minimized}" minimizedReason="{reply_minimized_reason}">')
            md_parts.append(f'<body>{reply_body}</body>')
            md_parts.append('</reply>')

        md_parts.append('</replies>')
        md_parts.append('</comment>')

    md_parts.append('</comments>')
    md_parts.append('</discussion>')

    # Join with newlines, but individual lines have no leading/trailing whitespace
    return "\n".join(md_parts)


@click.command()
@click.option('-r', '--repository', required=True, help='GitHub repository URL (e.g., https://github.com/owner/repo).')
@click.option('-o', '--output-dir', default='.', type=click.Path(file_okay=False, dir_okay=True, writable=True, resolve_path=True), help='Parent directory to save timestamped output subdirectories. Defaults to the current directory.')
@click.option('-t', '--token', default=None, help='GitHub Personal Access Token. Reads from GITHUB_TOKEN env var if not provided.')
@click.option('--since', default=None, type=str, help='Fetch discussions updated on or after this date (YYYY-MM-DD) or relative time (e.g., 1d, 5h, 2w, 1m, 1y).')
@click.option('--only-contributors', is_flag=True, default=False, help='If set, only fetch discussions involving repository contributors. Otherwise, fetch all discussions.')
@click.option('--llm-ready/--no-llm-ready', is_flag=True, default=True, help='Generate a .md file with XML-like structure for each discussion, suitable for LLMs. Enabled by default. Use --no-llm-ready to disable.')
def main(repository: str, output_dir: str, token: Optional[str], since: Optional[str], only_contributors: bool, llm_ready: bool):
    """
    Fetches discussions for a GitHub repository.
    By default, fetches all discussions. If --only-contributors is set,
    first fetches contributors and then finds discussions involving each contributor.
    Optionally filters discussions by update date using --since.
    Saves results into a timestamped subdirectory within the specified output directory.
    """
    parent_output_path = Path(output_dir)
    # Create a timestamped directory for this run, prefixed with "discussion_"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # run_output_dir_name = f"discussion_{timestamp}"
    run_output_dir_name = timestamp
    run_output_path = parent_output_path / run_output_dir_name
    run_output_path.mkdir(parents=True, exist_ok=True) # Ensure timestamped output directory exists

    # Add a file handler for this specific run
    log_file_path = run_output_path / "run.log"
    logger.add(log_file_path, level="DEBUG", rotation="10 MB", compression="zip") # Log DEBUG and above to a file

    since_date_str: Optional[str] = None
    if since:
        try:
            since_date_str = parse_since_to_date(since)
            logger.info(f"Filtering discussions updated on or after: {since_date_str} (parsed from '{since}')")
        except ValueError as e:
            logger.exception(f"Error parsing --since value: {e}")
            sys.exit(1)

    logger.info(f"Processing repository: {repository}")
    logger.info(f"Output will be saved to: {run_output_path}")
    logger.info(f"Detailed logs available at: {log_file_path}")

    try:
        parser = GithubParser(repository_url=repository, token=token)

        # 1. Get contributors (only needed if --only-contributors is false, but fetch anyway for now)
        #    If performance becomes an issue, move this fetch inside the 'if only_contributors:' block
        logger.info("Fetching contributors...")
        try:
            contributors = parser.get_contributors()
            contributor_logins: List[str] = [c.get('login') for c in contributors if c.get('login')]
            logger.info(f"Found {len(contributor_logins)} contributors.")
        except (ValueError, requests.exceptions.RequestException) as contrib_err:
            logger.exception(f"Failed to fetch contributors: {contrib_err}")
            # Decide if we should exit or continue without contributors if not strictly needed
            if only_contributors:
                logger.critical("Cannot proceed with --only-contributors flag without contributor list.")
                sys.exit(1)
            else:
                logger.warning("Could not fetch contributors. Proceeding without contributor information.")
                contributors = []
                contributor_logins = []
        except Exception as contrib_ex:
             logger.exception(f"An unexpected error occurred fetching contributors: {contrib_ex}")
             if only_contributors:
                 logger.critical("Cannot proceed with --only-contributors flag due to unexpected error.")
                 sys.exit(1)
             else:
                 logger.warning("Unexpected error fetching contributors. Proceeding without contributor information.")
                 contributors = []
                 contributor_logins = []

        # List to store (creation_datetime, markdown_content) tuples for final concatenation
        discussions_for_concatenation: List[Tuple[datetime, str]] = []

        processed_discussion_numbers = set() # Keep track of discussions already saved (by number)

        # Helper function to process and save a single discussion summary
        def process_and_save_discussion(discussion_summary: Dict[str, Any], generate_llm_md: bool):
            discussion_number = discussion_summary.get('number')
            if not discussion_number:
                logger.warning(f"Found discussion summary without a number: {discussion_summary.get('id')}")
                return

            # Check if already processed in this run
            if discussion_number in processed_discussion_numbers:
                logger.trace(f"Skipping discussion #{discussion_number} as it was already processed in this run.")
                return

            json_output_file = run_output_path / f"discussion_{discussion_number}.json"
            md_output_file = run_output_path / f"discussion_{discussion_number}.md"

            # Check if JSON file exists (e.g., from a previous incomplete run)
            # If LLM MD is requested, also check if it exists. Skip only if *both* exist or JSON exists and MD is not requested.
            json_exists = json_output_file.exists()
            md_exists = md_output_file.exists()

            if json_exists and (not generate_llm_md or md_exists):
                logger.info(f"Skipping discussion #{discussion_number} as required output file(s) already exist.")
                processed_discussion_numbers.add(discussion_number) # Mark as processed if files exist
                return
            elif json_exists and generate_llm_md and not md_exists:
                 logger.info(f"JSON for discussion #{discussion_number} exists, but LLM Markdown is missing. Will attempt to generate MD.")
                 # Need to fetch details again to generate MD
            elif not json_exists:
                 logger.debug(f"JSON for discussion #{discussion_number} does not exist. Will fetch details.")


            # Fetch full details (only if needed)
            discussion_details = None
            if not json_exists or (generate_llm_md and not md_exists):
                logger.debug(f"Fetching details for discussion #{discussion_number}...")
                try:
                    discussion_details = parser.get_discussion_details(discussion_number)
                except (ValueError, requests.exceptions.RequestException) as detail_err:
                    logger.error(f"Error fetching details for discussion #{discussion_number}: {detail_err}")
                    return # Skip this discussion if details cannot be fetched
                except Exception as detail_ex: # Catch unexpected errors during detail fetch
                    logger.exception(f"An unexpected error occurred fetching details for discussion #{discussion_number}: {detail_ex}")
                    return # Skip this discussion

            # Save the detailed discussion data to JSON (if it doesn't exist)
            if not json_exists and discussion_details:
                try:
                    with open(json_output_file, 'w', encoding='utf-8') as f:
                        json.dump(discussion_details, f, ensure_ascii=False, indent=2)
                    logger.info(f"Saved detailed discussion #{discussion_number} to {json_output_file}")
                    processed_discussion_numbers.add(discussion_number) # Mark as processed (at least JSON part)
                except IOError as io_err:
                    logger.error(f"Error writing JSON file {json_output_file}: {io_err}")
                    # Don't try to write MD if JSON failed
                    return
                except TypeError as type_err:
                    logger.error(f"Error serializing detailed data for discussion #{discussion_number} to JSON: {type_err}")
                    # Don't try to write MD if JSON failed
                    return

            # Generate and save LLM-ready Markdown if requested and details are available
            if generate_llm_md and discussion_details:
                if not md_exists: # Only generate if it doesn't exist yet
                    logger.debug(f"Generating LLM-ready Markdown for discussion #{discussion_number}...")
                    try:
                        markdown_content = generate_llm_ready_markdown(discussion_details)
                        with open(md_output_file, 'w', encoding='utf-8') as f:
                            f.write(markdown_content)
                        logger.info(f"Saved LLM-ready Markdown for discussion #{discussion_number} to {md_output_file}")

                        # Store content for final concatenation
                        created_at_str = discussion_details.get('createdAt')
                        if created_at_str:
                            try:
                                # Parse the ISO 8601 timestamp
                                created_at_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                                discussions_for_concatenation.append((created_at_dt, markdown_content))
                                logger.trace(f"Stored MD content for discussion #{discussion_number} for final concatenation.")
                            except (ValueError, TypeError) as date_err:
                                logger.warning(f"Could not parse createdAt timestamp '{created_at_str}' for discussion #{discussion_number}: {date_err}")
                        else:
                            logger.warning(f"Discussion #{discussion_number} details missing 'createdAt' field, cannot sort for concatenation.")

                    except IOError as io_err:
                        logger.error(f"Error writing Markdown file {md_output_file}: {io_err}")
                    except Exception as md_gen_err: # Catch errors during MD generation/writing
                        logger.exception(f"Error generating or writing LLM Markdown for discussion #{discussion_number}: {md_gen_err}")
                else:
                     logger.debug(f"LLM Markdown file already exists for discussion #{discussion_number}, skipping generation.")
                     # If MD exists, we might still need to add it to the concatenation list if it wasn't added before
                     # Check if we have details and it's not already added (tricky without tracking additions explicitly)
                     # For simplicity, we only add when *generating* the file. If the file exists, it won't be in the concatenated output unless the script is run again after deleting the concatenated file.
                     # Alternative: Read the existing MD file here and add it. Let's stick to the simpler approach for now.

            # If we only needed to generate MD and JSON already existed, mark as processed now.
            if json_exists and generate_llm_md and discussion_number not in processed_discussion_numbers:
                 processed_discussion_numbers.add(discussion_number)


            # --- Original code block for reference ---
            # try:
            #     discussion_details = parser.get_discussion_details(discussion_number)
            #     # Save the detailed discussion data
            #     try:
            #         with open(output_file, 'w', encoding='utf-8') as f:
            #             json.dump(discussion_details, f, ensure_ascii=False, indent=2)
            #         logger.info(f"Saved detailed discussion #{discussion_number} to {output_file}")
            #         processed_discussion_numbers.add(discussion_number) # Mark as processed
            #     except IOError as io_err:
            #         logger.error(f"Error writing file {output_file}: {io_err}")
            #     except TypeError as type_err:
            #         logger.error(f"Error serializing detailed data for discussion #{discussion_number} to JSON: {type_err}")

            # except (ValueError, requests.exceptions.RequestException) as detail_err:
            #     logger.error(f"Error fetching details for discussion #{discussion_number}: {detail_err}")
            # except Exception as detail_ex: # Catch unexpected errors during detail fetch/save
            #     logger.exception(f"An unexpected error occurred processing discussion #{discussion_number}: {detail_ex}")
            # --- End Original code block ---


        # --- Main Logic Branch ---
        if only_contributors:
            # Contributor list was fetched earlier. Check if it's usable.
            if not contributor_logins:
                 # This case should have been handled by sys.exit above if --only-contributors was set.
                 # Add a safeguard here just in case.
                 logger.critical("Contributor list is empty, cannot proceed with --only-contributors.")
                 sys.exit(1)

            logger.info(f"Searching discussions involving {len(contributor_logins)} contributors...")

            # 2. Search discussions involving each contributor
            for login in contributor_logins:
                logger.info(f"Searching for discussions involving '{login}'...")
                try:
                    per_page = 30
                    current_cursor = None
                    while True:
                        logger.debug(f"Fetching search results page for involves='{login}' (After: {current_cursor}, Since: {since_date_str or 'None'})...")
                        results = parser.get_discussions(
                            involves=login,
                            per_page=per_page,
                            after_cursor=current_cursor,
                            updated_after=since_date_str
                        )
                        search_results = results.get('data', {}).get('search', {})
                        if not search_results:
                            logger.warning(f"Unexpected GraphQL response structure when searching for involves='{login}'.")
                            break

                        items = search_results.get('nodes', [])
                        total_count = search_results.get('discussionCount', 0)
                        page_info = search_results.get('pageInfo', {})
                        has_next_page = page_info.get('hasNextPage', False)
                        next_cursor = page_info.get('endCursor')

                        logger.debug(f"GraphQL returned discussionCount={total_count} for involves='{login}'. Page info: hasNext={has_next_page}, endCursor={next_cursor}")

                        if not items:
                            if current_cursor is None: logger.info(f"No discussions found involving '{login}'.")
                            break # No items on this page

                        logger.debug(f"Processing {len(items)} discussions involving '{login}' found on this page (Total matching query: {total_count}).")
                        for discussion_summary in items:
                            process_and_save_discussion(discussion_summary, llm_ready) # Pass llm_ready flag

                        if not has_next_page or not next_cursor:
                            logger.debug(f"No more search result pages for involves='{login}'.")
                            break
                        current_cursor = next_cursor
                        # import time; time.sleep(1) # Optional delay

                except (ValueError, requests.exceptions.RequestException) as search_err:
                    logger.error(f"Error during discussion search involving '{login}': {search_err}")
                except Exception as search_ex: # Catch unexpected errors during search loop for one user
                    logger.exception(f"An unexpected error occurred during search for discussions involving '{login}': {search_ex}")

        else:
            # Fetch ALL discussions
            logger.info("Fetching all discussions in the repository...")
            try:
                per_page = 30
                current_cursor = None
                while True:
                    logger.debug(f"Fetching all discussions page (After: {current_cursor}, Since: {since_date_str or 'None'})...")
                    results = parser.get_discussions(
                        # No 'involves' filter
                        per_page=per_page,
                        after_cursor=current_cursor,
                        updated_after=since_date_str
                    )
                    search_results = results.get('data', {}).get('search', {})
                    if not search_results:
                        logger.warning("Unexpected GraphQL response structure when searching for all discussions.")
                        break

                    items = search_results.get('nodes', [])
                    total_count = search_results.get('discussionCount', 0)
                    page_info = search_results.get('pageInfo', {})
                    has_next_page = page_info.get('hasNextPage', False)
                    next_cursor = page_info.get('endCursor')

                    logger.debug(f"GraphQL returned discussionCount={total_count} for all discussions. Page info: hasNext={has_next_page}, endCursor={next_cursor}")

                    if not items:
                        if current_cursor is None: logger.info("No discussions found in the repository matching criteria.")
                        break # No items on this page

                    logger.debug(f"Processing {len(items)} discussions found on this page (Total matching query: {total_count}).")
                    for discussion_summary in items:
                        process_and_save_discussion(discussion_summary, llm_ready) # Pass llm_ready flag

                    if not has_next_page or not next_cursor:
                        logger.debug("No more search result pages for all discussions.")
                        break
                    current_cursor = next_cursor
                    # import time; time.sleep(1) # Optional delay

            except (ValueError, requests.exceptions.RequestException) as search_err:
                logger.error(f"Error during search for all discussions: {search_err}")
            except Exception as search_ex: # Catch unexpected errors during the main search loop
                logger.exception(f"An unexpected error occurred during search for all discussions: {search_ex}")

        # --- Concatenate LLM-ready Markdown Files ---
        if llm_ready and discussions_for_concatenation:
            logger.info("Concatenating LLM-ready Markdown files...")
            # Sort discussions by creation date (ascending)
            discussions_for_concatenation.sort(key=lambda item: item[0])

            concatenated_md_file_path = run_output_path / "all_discussions_llm_ready.md"
            try:
                with open(concatenated_md_file_path, 'w', encoding='utf-8') as outfile:
                    for i, (dt, md_content) in enumerate(discussions_for_concatenation):
                        outfile.write(md_content)
                        # Add a separator between discussions, but not after the last one
                        if i < len(discussions_for_concatenation) - 1:
                            outfile.write("\n\n---\n\n") # Using Markdown horizontal rule as separator
                logger.info(f"Successfully concatenated {len(discussions_for_concatenation)} discussions into {concatenated_md_file_path}")
            except IOError as io_err:
                logger.error(f"Error writing concatenated Markdown file {concatenated_md_file_path}: {io_err}")
            except Exception as concat_err:
                logger.exception(f"An unexpected error occurred during Markdown concatenation: {concat_err}")
        elif llm_ready:
            logger.info("LLM-ready output was enabled, but no discussion content was generated or collected for concatenation.")


        # --- Summary ---
        logger.info("\n--- Run Summary ---")
        total_processed = len(processed_discussion_numbers)
        if total_processed > 0:
            logger.info(f"Successfully processed {total_processed} unique discussions.")
            logger.info(f"Individual discussion JSON/MD files saved to: {run_output_path}")
            if llm_ready and discussions_for_concatenation and concatenated_md_file_path.exists():
                 logger.info(f"Concatenated LLM-ready Markdown saved to: {concatenated_md_file_path}")
        else:
            logger.info("No discussions were successfully processed or saved.")

        logger.info(f"Detailed logs available at: {log_file_path}")

    except (ValueError, requests.exceptions.RequestException) as e:
        # Catch errors during initial parser setup or other broad exceptions
        logger.exception(f"A critical error occurred during setup or initial API interaction: {e}")
        sys.exit(1)
    except Exception as e: # Catch any other unexpected errors
        logger.exception(f"An unexpected critical error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
