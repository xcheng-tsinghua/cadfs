"""Runnable examples for the high-level CADFS/Onshape APIs.

Examples:
    python main.py link-to-cadfs
    python main.py link-to-cadfs --url "https://cad.onshape.com/documents/..."
    python main.py batch-links-to-cadfs
    python main.py batch-step
    python main.py batch-step --overwrite
    python main.py end-to-end
"""

import argparse
import json
from pathlib import Path

from src import batch_download_steps, batch_onshape_links_to_cadfs, onshape_link_to_cadfs

PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS = PROJECT_ROOT / 'creds' / 'creds.json'
URLS_FILE = PROJECT_ROOT / 'example_data' / 'onshape_part_url.json'
EXAMPLE_CADFS_DIR = PROJECT_ROOT / 'example_data' / 'cadfs'
GENERATED_CADFS_DIR = PROJECT_ROOT / 'example_data' / 'batch_generated_cadfs'


def load_example_urls() -> list[str]:
    """Load the test Part Studio URLs supplied in example_data."""
    urls = json.loads(URLS_FILE.read_text(encoding='utf-8'))
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ValueError(f'{URLS_FILE} must contain a JSON list of URL strings')
    return urls


def example_link_to_cadfs(onshape_url: str | None = None) -> str:
    """Example 1: convert one Onshape Part Studio link into CADFS code."""
    url = onshape_url or load_example_urls()[0]
    cadfs_code = onshape_link_to_cadfs(url, credentials=CREDENTIALS)

    output_path = PROJECT_ROOT / 'example_data' / 'generated_cadfs' / 'single_part.txt'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cadfs_code, encoding='utf-8')
    print(f'CADFS code saved to: {output_path}')
    return cadfs_code


def example_batch_download_steps(*, overwrite: bool = False):
    """Example 2: convert every CADFS text file in a directory to STEP."""
    results = batch_download_steps(
        EXAMPLE_CADFS_DIR,
        output_dir=PROJECT_ROOT / 'example_data' / 'step_output',
        credentials=CREDENTIALS,
        workers=1,
        overwrite=overwrite,
    )
    print_results(results)
    return results


def example_batch_onshape_links_to_cadfs(*, overwrite: bool = False):
    """Example 3: convert every Onshape link in a JSON file to a CADFS text file."""
    results = batch_onshape_links_to_cadfs(
        URLS_FILE,
        output_dir=GENERATED_CADFS_DIR,
        credentials=CREDENTIALS,
        overwrite=overwrite,
    )
    print_results(results)
    return results


def example_end_to_end(*, overwrite: bool = False):
    """Example 4: JSON links -> CADFS directory -> STEP directory."""
    conversion_results = batch_onshape_links_to_cadfs(
        URLS_FILE,
        output_dir=GENERATED_CADFS_DIR,
        credentials=CREDENTIALS,
        overwrite=overwrite,
    )
    print_results(conversion_results)

    results = batch_download_steps(
        GENERATED_CADFS_DIR,
        output_dir=PROJECT_ROOT / 'example_data' / 'end_to_end_step_output',
        credentials=CREDENTIALS,
        workers=1,
        overwrite=overwrite,
    )
    print_results(results)
    return results


def print_results(results) -> None:
    """Print one concise line for each STEP download result."""
    for result in results:
        message = f'{result.name}: {result.status}'
        if result.path is not None:
            message += f' -> {result.path}'
        if result.error:
            message += f' ({result.error})'
        print(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'example',
        choices=('link-to-cadfs', 'batch-links-to-cadfs', 'batch-step', 'end-to-end'),
        help='which example to run',
    )
    parser.add_argument(
        '--url',
        help='Onshape Part Studio URL for link-to-cadfs; defaults to the first example URL',
    )
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='regenerate STEP files that already exist',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.example == 'link-to-cadfs':
        example_link_to_cadfs(args.url)
    elif args.example == 'batch-links-to-cadfs':
        example_batch_onshape_links_to_cadfs(overwrite=args.overwrite)
    elif args.example == 'batch-step':
        example_batch_download_steps(overwrite=args.overwrite)
    else:
        example_end_to_end(overwrite=args.overwrite)


if __name__ == '__main__':
    # main()
    batch_onshape_links_to_cadfs()
