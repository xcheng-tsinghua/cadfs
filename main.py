"""Runnable examples for the high-level CADFS/Onshape APIs.

Examples:
    python main.py link-to-cadfs
    python main.py link-to-cadfs --url "https://cad.onshape.com/documents/..."
    python main.py batch-step
    python main.py batch-step --overwrite
    python main.py end-to-end
"""

import argparse
import json
from pathlib import Path

from src import batch_download_steps, onshape_link_to_cadfs

PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS = PROJECT_ROOT / 'creds' / 'creds.json'
URLS_FILE = PROJECT_ROOT / 'example_data' / 'onshape_part_url.json'
EXAMPLE_CADFS_DIR = PROJECT_ROOT / 'example_data' / 'cadfs'


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
    """Example 2: load multiple CADFS text files and download their STEP models."""
    cadfs_codes = {path.stem: path.read_text(encoding='utf-8') for path in sorted(EXAMPLE_CADFS_DIR.glob('*.txt'))}
    if not cadfs_codes:
        raise FileNotFoundError(f'No CADFS .txt files found in {EXAMPLE_CADFS_DIR}')

    results = batch_download_steps(
        cadfs_codes,
        output_dir=PROJECT_ROOT / 'example_data' / 'step_output',
        credentials=CREDENTIALS,
        workers=1,
        overwrite=overwrite,
    )
    print_results(results)
    return results


def example_end_to_end(*, overwrite: bool = False):
    """Example 3: convert all example links and immediately download their STEP files."""
    cadfs_codes = {}
    for index, url in enumerate(load_example_urls()):
        # onshape_link_to_cadfs prints the request count for this URL.
        cadfs_codes[f'onshape_part_{index:02d}'] = onshape_link_to_cadfs(url, credentials=CREDENTIALS)

    # batch_download_steps prints the total request count for the entire batch.
    results = batch_download_steps(
        cadfs_codes,
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
        choices=('link-to-cadfs', 'batch-step', 'end-to-end'),
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
    elif args.example == 'batch-step':
        example_batch_download_steps(overwrite=args.overwrite)
    else:
        example_end_to_end(overwrite=args.overwrite)


if __name__ == '__main__':
    main()
