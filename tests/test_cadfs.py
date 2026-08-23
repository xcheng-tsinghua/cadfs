import base64
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src.cadfs import (
    _extract_featurescript_source,
    batch_download_steps,
    onshape_link_to_cadfs,
    parse_onshape_part_url,
)
from src.fs_parser.query import rewrite_qcompressed


class OnshapeUrlTests(unittest.TestCase):
    def test_parse_workspace_url_and_query(self):
        ref = parse_onshape_part_url(
            'https://cad.onshape.com/documents/doc/w/work/e/element?configuration=List_1%3DOne&linkDocumentId=linked'
        )

        self.assertEqual(ref.document_id, 'doc')
        self.assertEqual(ref.wvm, 'w')
        self.assertEqual(ref.wvm_id, 'work')
        self.assertEqual(ref.element_id, 'element')
        self.assertEqual(ref.configuration, 'List_1=One')
        self.assertEqual(ref.link_document_id, 'linked')

    def test_parse_enterprise_version_url(self):
        ref = parse_onshape_part_url('https://acme.onshape.com/documents/doc/v/version/e/element')

        self.assertEqual(ref.stack, 'https://acme.onshape.com')
        self.assertEqual(ref.wvm, 'v')

    def test_reject_non_partstudio_url(self):
        with self.assertRaises(ValueError):
            parse_onshape_part_url('https://cad.onshape.com/documents/doc/w/work')


class FeatureScriptResponseTests(unittest.TestCase):
    def test_extracts_largest_nested_source(self):
        expected = 'FeatureScript 1; features.foo = function(id) { if (true) {} }'
        payload = {
            'source': 'FeatureScript 1;',
            'toBeParsed': {'source': expected},
        }

        self.assertEqual(_extract_featurescript_source(payload), expected)

    def test_unparses_modern_ast_response(self):
        payload = {
            'btType': 'BTPModule-1',
            'version': {'btType': 'BTPLiteralNumber-1', 'text': '3044', 'value': 3044},
            'topLevel': [],
        }

        self.assertEqual(_extract_featurescript_source(payload), 'FeatureScript 3044;\n\n')

    def test_expands_modern_qcompressed_wrapper(self):
        expanded = '%B5$QueryM1S11$operationIdS12$FrontplaneOpS9$queryTypeS5$DUMMY'
        encoded = base64.b64encode(zlib.compress(expanded.encode())).decode().rstrip('=')
        line = f'Q0=qCompressed(1.0,"&2ae${encoded}",id);'

        self.assertEqual(
            rewrite_qcompressed(line),
            'Q0=qCreatedBy(makeId("Front.planeOp"), EntityType.FACE);',
        )


class LinkConversionTests(unittest.TestCase):
    def test_reports_requests_after_success(self):
        class FakeResponse:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        class FakeClient:
            def __init__(self, **_kwargs):
                self.requests_made = 0

            def get_partstudio_featurescript(self, *_args, **_kwargs):
                self.requests_made += 1
                return FakeResponse({'source': 'FeatureScript 1; features.foo = function(id) { if (true) {} }'})

            def get_sketch_information_wvm(self, *_args, **_kwargs):
                self.requests_made += 1
                return FakeResponse({'sketches': []})

        with patch('src.cadfs.Client', FakeClient), patch('src.fs_parser.parser.Parser') as parser:
            parser.return_value.process_text.return_value = ('FeatureScript cleaned', [])
            output = StringIO()
            with redirect_stdout(output):
                code = onshape_link_to_cadfs(
                    'https://cad.onshape.com/documents/doc/w/work/e/element',
                    credentials=('access', 'secret'),
                )

        self.assertEqual(code, 'FeatureScript cleaned')
        self.assertIn('used 2 Onshape request(s)', output.getvalue())

    def test_reports_zero_requests_for_invalid_url(self):
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(ValueError):
            onshape_link_to_cadfs('invalid', credentials=('access', 'secret'))

        self.assertIn('used 0 Onshape request(s)', output.getvalue())


class BatchDownloadTests(unittest.TestCase):
    def test_batch_preserves_names_and_reports_per_item_status(self):
        def fake_render(files, _input_dir, output_dir, *_args, **_kwargs):
            request_hook = _kwargs['request_hook']
            for file in files:
                request_hook()
                if file.stem == '00000000':
                    (output_dir / f'{file.stem}.step').write_bytes(b'STEP')
                else:
                    (output_dir / f'{file.stem}_c.txt').write_text('compile error', encoding='utf-8')
            return len(files)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('src.cadfs.render_files_with_api_session', side_effect=fake_render):
                output = StringIO()
                with redirect_stdout(output):
                    results = batch_download_steps(
                        {'good model': 'FeatureScript good', 'bad model': 'FeatureScript bad'},
                        temp_dir,
                        credentials=('access', 'secret'),
                        workers=2,
                    )

            self.assertEqual([result.status for result in results], ['success', 'compile_error'])
            self.assertEqual((Path(temp_dir) / 'good_model.step').read_bytes(), b'STEP')
            self.assertTrue((Path(temp_dir) / 'bad_model_c.txt').exists())
            self.assertIn('used 2 Onshape request(s)', output.getvalue())

    def test_existing_step_is_skipped_without_rendering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = Path(temp_dir) / '00000000.step'
            existing.write_bytes(b'existing')
            with patch('src.cadfs.render_files_with_api_session') as render:
                results = batch_download_steps(
                    ['FeatureScript code'],
                    temp_dir,
                    credentials=('access', 'secret'),
                )

            self.assertEqual(results[0].status, 'skipped')
            render.assert_not_called()

    def test_empty_batch_reports_zero_requests(self):
        output = StringIO()
        with redirect_stdout(output):
            results = batch_download_steps([], 'unused', credentials=('access', 'secret'))

        self.assertEqual(results, [])
        self.assertIn('used 0 Onshape request(s)', output.getvalue())


if __name__ == '__main__':
    unittest.main()
