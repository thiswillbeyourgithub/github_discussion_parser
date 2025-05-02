# GitHub Discussion Parser for LLMs

This project provides a Python script (`discussion_parser.py`) to fetch discussions from a GitHub repository and format them into individual XML-like Markdown files. The primary goal is to make the entire discussion history of a repository easily accessible and digestible for Large Language Models (LLMs).

Each discussion, along with its comments and replies, is saved as a separate `.md` file. This structured format helps LLMs understand the context and flow of conversations within the repository's discussions.

## Features

*   Fetches all discussions or filters them based on various criteria (update date, involvement of contributors).
*   Retrieves detailed discussion content, including the main post, comments, and nested replies.
*   Outputs each discussion into two files by default:
    *   A `.json` file containing the raw detailed data fetched from the GitHub API.
    *   A `.md` file formatted with XML-like tags (e.g., `<discussion>`, `<post>`, `<comment>`, `<reply>`) containing the discussion text, suitable for LLM processing.
*   When LLM-ready Markdown generation is enabled (default), it also creates a single `all_discussions_llm_ready.md` file concatenating all individual discussion `.md` files, sorted by creation date.
*   Organizes output into timestamped directories (format `YYYYMMDD_HHMMSS`) for easy tracking of runs.
*   Handles GitHub API pagination and potential errors gracefully.
*   Uses Loguru for clear logging to both console and a run-specific log file.

## Prerequisites

*   Python 3.8+
*   Required Python packages listed in `requirements.txt`. Install them using pip:
    ```bash
    pip install -r requirements.txt
    ```
*   A GitHub Personal Access Token (PAT) with `repo` scope (or at least `public_repo` for public repositories). You can provide this token via the `--token` argument or by setting the `GITHUB_TOKEN` environment variable.

## Usage

The main script is `discussion_parser.py`. You run it from the command line, providing the target repository URL and options.

**Basic Usage (Fetch all discussions, generate JSON and LLM-ready Markdown for each, and a concatenated Markdown file):**

```bash
python discussion_parser.py -r https://github.com/owner/repo
```
*(Note: `--llm-ready` is enabled by default)*

**Command-Line Options:**

*   `-r`, `--repository` (Required): The URL of the GitHub repository (e.g., `https://github.com/owner/repo`).
*   `-o`, `--output-dir` (Optional): Parent directory to save timestamped output subdirectories. Defaults to the current directory (`.`).
*   `-t`, `--token` (Optional): Your GitHub Personal Access Token. If not provided, the script will look for the `GITHUB_TOKEN` environment variable.
*   `--since` (Optional): Fetch discussions updated on or after a specific date (YYYY-MM-DD) or relative time (e.g., `7d` for 7 days ago, `2w` for 2 weeks, `1m` for 1 month).
*   `--only-contributors` (Optional Flag): If set, only fetch discussions involving repository contributors (users who have committed to the repo). Otherwise, fetch all discussions.
*   `--llm-ready` / `--no-llm-ready` (Optional Flag): Controls the generation of `.md` files with XML-like structure and the final concatenated `all_discussions_llm_ready.md` file. Enabled by default (`--llm-ready`). Use `--no-llm-ready` to disable Markdown generation and only create `.json` files.

**Example: Fetch discussions updated in the last 30 days from a specific repo, generating only JSON:**

```bash
# Set the token as an environment variable (recommended)
export GITHUB_TOKEN="your_github_pat_here"

# Run the script, explicitly disabling LLM-ready output
python discussion_parser.py \
    -r https://github.com/some-owner/some-repo \
    --since 30d \
    --no-llm-ready \
    -o ./output_data
```

**Example: Fetch only discussions involving contributors, generating both JSON and LLM-ready Markdown (default behavior):**

```bash
python discussion_parser.py \
    -r https://github.com/another-owner/another-repo \
    --only-contributors \
    -t your_github_pat_here
```
*(Note: `--llm-ready` is implied as it's the default)*

Output files will be saved in a subdirectory named with the timestamp format `YYYYMMDD_HHMMSS` within the specified output directory (or the current directory if `-o` is omitted). This directory will contain individual `.json` and (by default) `.md` files for each discussion (e.g., `discussion_123.json`, `discussion_123.md`), plus a `run.log` file and (by default) the `all_discussions_llm_ready.md` file.

---

*This README was generated with assistance from [aider.chat](https://github.com/Aider-AI/aider/issues).*
