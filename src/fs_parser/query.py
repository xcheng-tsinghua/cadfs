import base64
import re
import zlib

from src.fs_parser.exceptions import NotImplementedQueryError

_DEFAULT_PLANES = ['Front.planeOp', 'Top.planeOp', 'Right.planeOp', 'Origin.pointOp']


def rewrite_dummy_query(line: str) -> str:
    """Rewrite a dummyQuery(...) into the equivalent qCreatedBy(...) call."""
    # TODO: use regular expression to catch args
    first, second = line.split('dummyQuery')
    f_position = second.find('"')
    s_position = second.find('"', f_position + 1)
    feature_name = second[f_position : s_position + 1]
    if feature_name[1:-1] in _DEFAULT_PLANES:
        feature_name = 'makeId(' + feature_name + ')'
    else:
        feature_name = 'id+' + feature_name
    line = first + 'qCreatedBy(' + feature_name + second[s_position + 1 :]
    return line


def rewrite_qcompressed(line: str) -> str:
    """Rewrite default-plane queries and preserve other relative compressed queries.

    Modern Onshape FeatureScript representations use ``qCompressed`` for many
    history-based selections. Those payloads remain relative to the supplied
    feature ``id`` and can be reused in the generated custom feature unchanged.
    """
    match = re.search(r'qCompressed\([^,]+,\s*"([^"]+)"', line)
    if match and match.group(1).startswith('&'):
        payload = match.group(1)
        try:
            encoded = payload.split('$', 1)[1]
            padding = '=' * (-len(encoded) % 4)
            expanded = zlib.decompress(base64.b64decode(encoded + padding)).decode('utf-8')
        except (IndexError, ValueError, UnicodeDecodeError, zlib.error) as exc:
            raise NotImplementedQueryError(f'Invalid compressed query: {line}') from exc
        line = line[: match.start(1)] + expanded + line[match.end(1) :]

    if 'DUMMY' in line:
        if 'FrontplaneOp' in line:
            op = 'Front.planeOp'
        elif 'TopplaneOp' in line:
            op = 'Top.planeOp'
        elif 'RightplaneOp' in line:
            op = 'Right.planeOp'
        else:
            raise NotImplementedQueryError(f'{line}')
        line = line.split('qCompressed')[0] + f'qCreatedBy(makeId("{op}"), EntityType.FACE);'
    elif 'qCompressed(' not in line:
        raise NotImplementedQueryError(f'{line}')
    return line


def rewrite_makequery(line: str) -> str:
    """Pass-through hook for makeQuery lines (currently returns the line unchanged)."""
    return line
