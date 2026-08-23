"""High-level conversion helpers for CADFS and Onshape."""

import json
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from src.api.account import AccountQueue
from src.api.client import Client
from src.rendering.session import render_files_with_api_session

Credentials = str | Path | Mapping[str, str] | tuple[str, str]
StepStatus = Literal['success', 'skipped', 'compile_error', 'render_error', 'failed']


@dataclass(frozen=True)
class OnshapePartRef:
    """Identifiers extracted from an Onshape Part Studio URL."""

    stack: str
    document_id: str
    wvm: Literal['w', 'v', 'm']
    wvm_id: str
    element_id: str
    configuration: str | None = None
    link_document_id: str | None = None


@dataclass(frozen=True)
class StepDownloadResult:
    """Outcome for one item passed to :func:`batch_download_steps`."""

    name: str
    status: StepStatus
    path: Path | None = None
    error: str | None = None


def parse_onshape_part_url(url: str) -> OnshapePartRef:
    """Parse a Part Studio URL containing ``/documents/.../{w|v|m}/.../e/...``."""
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError(f'Invalid Onshape URL: {url!r}')

    parts = [part for part in parsed.path.split('/') if part]
    try:
        document_index = parts.index('documents')
        document_id = parts[document_index + 1]
        wvm = parts[document_index + 2]
        wvm_id = parts[document_index + 3]
        if parts[document_index + 4] != 'e':
            raise ValueError
        element_id = parts[document_index + 5]
    except (ValueError, IndexError) as exc:
        raise ValueError(
            'Expected an Onshape Part Studio link like https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>'
        ) from exc

    if wvm not in {'w', 'v', 'm'}:
        raise ValueError(f'Unsupported Onshape document selector {wvm!r}; expected w, v, or m')

    query = parse_qs(parsed.query)
    return OnshapePartRef(
        stack=f'{parsed.scheme}://{parsed.netloc}',
        document_id=document_id,
        wvm=wvm,
        wvm_id=wvm_id,
        element_id=element_id,
        configuration=_first_query_value(query, 'configuration'),
        link_document_id=_first_query_value(query, 'linkDocumentId'),
    )


def onshape_link_to_cadfs(
    onshape_url: str,
    credentials: Credentials = 'creds/onshape_accounts.json',
    *,
    api_version: int = 12,
) -> str:
    """Download a Part Studio's history and convert it to cleaned CADFS code.

    The URL may address a workspace (``w``), version (``v``), or microversion
    (``m``). The caller's API key must have permission to view the document.
    Unsupported CAD operations raise the same parser exceptions as
    :class:`src.fs_parser.parser.Parser`.
    """
    client = None
    try:
        ref = parse_onshape_part_url(onshape_url)
        accounts = _load_accounts(credentials, ref.stack)
        access_key, secret_key = accounts[0]
        client = Client(
            stack=ref.stack,
            logging=False,
            version=api_version,
            creds={'access_key': access_key, 'secret_key': secret_key},
        )
        source_response = client.get_partstudio_featurescript(
            ref.document_id,
            ref.wvm,
            ref.wvm_id,
            ref.element_id,
            configuration=ref.configuration,
            link_document_id=ref.link_document_id,
        )
        sketch_response = client.get_sketch_information_wvm(
            ref.document_id,
            ref.wvm,
            ref.wvm_id,
            ref.element_id,
            configuration=ref.configuration,
            link_document_id=ref.link_document_id,
        )
        _raise_api_error(source_response, 'get FeatureScript representation')
        _raise_api_error(sketch_response, 'get sketch information')

        source = _extract_featurescript_source(source_response.json())
        sketch_data = sketch_response.json()
        if not isinstance(sketch_data, dict) or 'sketches' not in sketch_data:
            raise ValueError('Onshape sketch response does not contain a sketches list')

        with tempfile.TemporaryDirectory(prefix='cadfs_parse_') as temp_dir:
            # Import lazily so URL/credential helpers and STEP rendering do not require
            # the parser's numerical dependencies until conversion is requested.
            from src.fs_parser.parser import Parser

            temp_path = Path(temp_dir)
            source_path = temp_path / 'partstudio.fs'
            sketches_path = temp_path / 'sketches.json'
            source_path.write_text(source, encoding='utf-8')
            sketches_path.write_text(json.dumps(sketch_data), encoding='utf-8')
            code, _operations = Parser(
                str(source_path),
                str(sketches_path),
                preserve_identifiers=True,
                preserve_operations=True,
            ).process_text()
        return code
    finally:
        requests_used = client.requests_made if client is not None else 0
        print(f'onshape_link_to_cadfs used {requests_used} Onshape request(s)')


def batch_download_steps(
    cadfs_codes: Sequence[str] | Mapping[str, str],
    output_dir: str | Path,
    credentials: Credentials = 'creds/onshape_accounts.json',
    *,
    names: Sequence[str] | None = None,
    workers: int = 4,
    api_version: int = 12,
    overwrite: bool = False,
) -> list[StepDownloadResult]:
    """Render CADFS programs in Onshape and download their STEP models.

    ``cadfs_codes`` can be a sequence, in which case files are named
    ``00000000.step``, ``00000001.step``, ...; or a ``name -> code`` mapping.
    Explicit ``names`` may be supplied with a sequence. One temporary Onshape
    document is used per worker and is deleted when that worker finishes.

    The returned list has one result per input and preserves input order. A bad
    program does not abort the remaining batch; its status is ``compile_error``,
    ``render_error``, or ``failed``.
    """
    requests_used = 0
    try:
        items = _normalize_codes(cadfs_codes, names)
        if workers < 1:
            raise ValueError('workers must be at least 1')
        if api_version < 1:
            raise ValueError('api_version must be positive')
        if not items:
            return []

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        accounts = _load_accounts(credentials)

        results: list[StepDownloadResult | None] = [None] * len(items)
        pending: list[tuple[int, str, str]] = []
        for index, (name, code) in enumerate(items):
            destination = output_path / f'{name}.step'
            if destination.exists() and not overwrite:
                results[index] = StepDownloadResult(name=name, status='skipped', path=destination)
            else:
                if overwrite:
                    for stale_path in (
                        destination,
                        output_path / f'{name}_c.txt',
                        output_path / f'{name}_r.txt',
                    ):
                        stale_path.unlink(missing_ok=True)
                pending.append((index, name, code))

        if pending:
            requests_used = _render_pending_codes(pending, output_path, accounts, results, workers, api_version)
        return [result for result in results if result is not None]
    finally:
        print(f'batch_download_steps used {requests_used} Onshape request(s)')


def _render_pending_codes(
    pending: list[tuple[int, str, str]],
    output_dir: Path,
    accounts: list[tuple[str, str]],
    results: list[StepDownloadResult | None],
    workers: int,
    api_version: int,
) -> int:
    """Render pending programs through the repository's existing session pipeline."""
    account_queue = AccountQueue(accounts)
    worker_count = min(workers, len(accounts), len(pending))
    requests_used = 0
    request_lock = threading.Lock()

    def count_request() -> None:
        nonlocal requests_used
        with request_lock:
            requests_used += 1

    with tempfile.TemporaryDirectory(prefix='cadfs_batch_') as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / 'input'
        rendered_path = temp_path / 'rendered'
        input_path.mkdir()
        rendered_path.mkdir()

        indexed_files: list[Path] = []
        for original_index, _name, code in pending:
            filename = f'{original_index:08}.txt'
            (input_path / filename).write_text(code, encoding='utf-8')
            indexed_files.append(Path(filename))

        groups = _split_evenly(indexed_files, worker_count)
        threads = []
        render_errors: dict[str, str] = {}
        for thread_id, files in enumerate(groups):
            thread = threading.Thread(
                target=_render_file_group,
                args=(
                    files,
                    input_path,
                    rendered_path,
                    account_queue,
                    thread_id,
                    api_version,
                    render_errors,
                    count_request,
                ),
                name=f'CADFS-Step-{thread_id}',
            )
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()

        for original_index, name, _code in pending:
            source_stem = f'{original_index:08}'
            step_source = rendered_path / f'{source_stem}.step'
            compile_error = rendered_path / f'{source_stem}_c.txt'
            render_error = rendered_path / f'{source_stem}_r.txt'
            if step_source.exists():
                destination = output_dir / f'{name}.step'
                shutil.copyfile(step_source, destination)
                results[original_index] = StepDownloadResult(name=name, status='success', path=destination)
            elif compile_error.exists():
                marker = output_dir / f'{name}_c.txt'
                shutil.copyfile(compile_error, marker)
                results[original_index] = StepDownloadResult(
                    name=name,
                    status='compile_error',
                    path=marker,
                    error=compile_error.read_text(encoding='utf-8'),
                )
            elif render_error.exists():
                marker = output_dir / f'{name}_r.txt'
                shutil.copyfile(render_error, marker)
                results[original_index] = StepDownloadResult(
                    name=name,
                    status='render_error',
                    path=marker,
                    error=render_error.read_text(encoding='utf-8'),
                )
            else:
                results[original_index] = StepDownloadResult(
                    name=name,
                    status='failed',
                    error=render_errors.get(
                        source_stem,
                        'No STEP file or error marker was produced; check logs and API quota',
                    ),
                )
    return requests_used


def _render_file_group(
    files: list[Path],
    input_dir: Path,
    output_dir: Path,
    account_queue: AccountQueue,
    thread_id: int,
    api_version: int,
    errors: dict[str, str],
    request_hook,
) -> None:
    account = account_queue.get_account(timeout=5.0)
    if account is None:
        for file in files:
            errors[file.stem] = 'No Onshape account was available for this worker'
        return
    try:
        render_files_with_api_session(
            files,
            input_dir,
            output_dir,
            account,
            thread_id,
            export=True,
            api_version=api_version,
            request_hook=request_hook,
        )
    except Exception as exc:
        for file in files:
            errors[file.stem] = f'{type(exc).__name__}: {exc}'
    finally:
        if account.has_quota():
            account_queue.return_account(account)


def _split_evenly(items: list[Path], groups: int) -> list[list[Path]]:
    quotient, remainder = divmod(len(items), groups)
    result = []
    cursor = 0
    for group_index in range(groups):
        size = quotient + (1 if group_index < remainder else 0)
        result.append(items[cursor : cursor + size])
        cursor += size
    return result


def _normalize_codes(
    cadfs_codes: Sequence[str] | Mapping[str, str], names: Sequence[str] | None
) -> list[tuple[str, str]]:
    if isinstance(cadfs_codes, Mapping):
        if names is not None:
            raise ValueError('names cannot be used when cadfs_codes is a mapping')
        raw_items = list(cadfs_codes.items())
    else:
        if isinstance(cadfs_codes, (str, bytes)):
            raise TypeError('cadfs_codes must be a sequence of programs, not a single string')
        codes = list(cadfs_codes)
        if names is not None and len(names) != len(codes):
            raise ValueError('names and cadfs_codes must have the same length')
        raw_names = list(names) if names is not None else [f'{index:08}' for index in range(len(codes))]
        raw_items = list(zip(raw_names, codes))

    items = [(_safe_name(str(name)), code) for name, code in raw_items]
    if any(not isinstance(code, str) or not code.strip() for _name, code in items):
        raise ValueError('every CADFS program must be a non-empty string')
    normalized_names = [name for name, _code in items]
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError('output names must be unique after sanitization')
    return items


def _safe_name(name: str) -> str:
    stem = Path(name).stem
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).strip('._')
    if not safe:
        raise ValueError(f'Invalid output name: {name!r}')
    return safe


def _load_accounts(credentials: Credentials, stack: str = 'https://cad.onshape.com') -> list[tuple[str, str]]:
    if isinstance(credentials, tuple):
        if len(credentials) != 2:
            raise ValueError('credential tuple must be (access_key, secret_key)')
        accounts = [(str(credentials[0]), str(credentials[1]))]
    elif isinstance(credentials, Mapping):
        accounts = _accounts_from_json(dict(credentials), stack)
    else:
        credential_path = Path(credentials)
        if not credential_path.exists():
            raise FileNotFoundError(f'Credentials path does not exist: {credential_path}')
        if credential_path.is_dir():
            accounts = []
            for json_path in sorted(credential_path.glob('*.json')):
                accounts.extend(_accounts_from_json(json.loads(json_path.read_text(encoding='utf-8')), stack))
        else:
            accounts = _accounts_from_json(json.loads(credential_path.read_text(encoding='utf-8')), stack)
    if not accounts:
        raise ValueError('No Onshape API accounts found in credentials')
    return accounts


def _accounts_from_json(data: dict, stack: str) -> list[tuple[str, str]]:
    if 'access_key' in data and 'secret_key' in data:
        return [(str(data['access_key']), str(data['secret_key']))]
    stack_data = data.get(stack)
    if stack_data is None:
        target_host = urlparse(stack).netloc.lower()
        for candidate_stack, candidate_data in data.items():
            if urlparse(str(candidate_stack)).netloc.lower() == target_host:
                stack_data = candidate_data
                break
    if isinstance(stack_data, dict) and 'access_key' in stack_data and 'secret_key' in stack_data:
        return [(str(stack_data['access_key']), str(stack_data['secret_key']))]
    if all(isinstance(value, str) for value in data.values()):
        return [(str(access_key), str(secret_key)) for access_key, secret_key in data.items()]
    raise ValueError('Unsupported credentials JSON format')


def _extract_featurescript_source(payload: object) -> str:
    candidates: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            source = value.get('source')
            if isinstance(source, str):
                candidates.append(source)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    usable = [source for source in candidates if 'FeatureScript' in source and 'function(id)' in source]
    if usable:
        return max(usable, key=len)
    if isinstance(payload, dict) and payload.get('btType', '').startswith('BTPModule'):
        from src.fs_parser.unparser import unparse_featurescript_ast

        return unparse_featurescript_ast(payload)
    raise ValueError('Onshape response contains neither FeatureScript source nor a supported BTPModule AST')


def _raise_api_error(response, action: str) -> None:
    if 200 <= response.status_code < 300:
        return
    detail = response.text[:500]
    raise RuntimeError(f'Onshape API failed to {action}: HTTP {response.status_code}: {detail}')


def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


__all__ = [
    'OnshapePartRef',
    'StepDownloadResult',
    'batch_download_steps',
    'onshape_link_to_cadfs',
    'parse_onshape_part_url',
]
