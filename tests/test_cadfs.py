import base64
import json
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
    batch_onshape_links_to_cadfs,
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
            def __init__(self, **kwargs):
                self.request_hook = kwargs['request_hook']

            def get_partstudio_featurescript(self, *_args, **_kwargs):
                self.request_hook()
                return FakeResponse({'source': 'FeatureScript 1; features.foo = function(id) { if (true) {} }'})

            def get_sketch_information_wvm(self, *_args, **_kwargs):
                self.request_hook()
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
                    (output_dir / f'{file.stem}_c.txt').write_text('compile error', encoding='utf-8')
                else:
                    (output_dir / f'{file.stem}.step').write_bytes(b'STEP')
            return len(files)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / 'cadfs'
            output_dir = Path(temp_dir) / 'step'
            input_dir.mkdir()
            (input_dir / 'good model.txt').write_text('FeatureScript good', encoding='utf-8')
            (input_dir / 'bad model.txt').write_text('FeatureScript bad', encoding='utf-8')
            with patch('src.cadfs.render_files_with_api_session', side_effect=fake_render):
                output = StringIO()
                with redirect_stdout(output):
                    results = batch_download_steps(
                        input_dir,
                        output_dir,
                        credentials=('access', 'secret'),
                        workers=2,
                    )

            self.assertEqual([result.status for result in results], ['compile_error', 'success'])
            self.assertEqual((output_dir / 'good_model.step').read_bytes(), b'STEP')
            self.assertTrue((output_dir / 'bad_model_c.txt').exists())
            self.assertIn('used 2 Onshape request(s)', output.getvalue())

    def test_existing_step_is_skipped_without_rendering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / 'cadfs'
            output_dir = Path(temp_dir) / 'step'
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / '00000000.txt').write_text('FeatureScript code', encoding='utf-8')
            existing = output_dir / '00000000.step'
            existing.write_bytes(b'existing')
            with patch('src.cadfs.render_files_with_api_session') as render:
                results = batch_download_steps(
                    input_dir,
                    output_dir,
                    credentials=('access', 'secret'),
                )

            self.assertEqual(results[0].status, 'skipped')
            render.assert_not_called()

    def test_empty_batch_reports_zero_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / 'cadfs'
            input_dir.mkdir()
            output = StringIO()
            with redirect_stdout(output):
                results = batch_download_steps(input_dir, Path(temp_dir) / 'step')

        self.assertEqual(results, [])
        self.assertIn('used 0 Onshape request(s)', output.getvalue())


class BatchLinkConversionTests(unittest.TestCase):
    @staticmethod
    def fake_convert(url, _credentials, _api_version, request_hook):
        request_hook()
        request_hook()
        return f'CADFS for {url}'

    def test_converts_url_string_list_to_indexed_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / 'urls.json'
            output_dir = Path(temp_dir) / 'cadfs'
            urls = ['https://example.test/one', 'https://example.test/two']
            json_path.write_text(json.dumps(urls), encoding='utf-8')

            output = StringIO()
            with patch('src.cadfs._convert_onshape_link', side_effect=self.fake_convert), redirect_stdout(output):
                results = batch_onshape_links_to_cadfs(json_path, output_dir)

            self.assertEqual([result.status for result in results], ['success', 'success'])
            self.assertEqual((output_dir / '00000000.txt').read_text(encoding='utf-8'), f'CADFS for {urls[0]}')
            self.assertEqual((output_dir / '00000001.txt').read_text(encoding='utf-8'), f'CADFS for {urls[1]}')
            self.assertIn('used 4 Onshape request(s)', output.getvalue())

    def test_accepts_list_of_objects_with_url_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / 'urls.json'
            output_dir = Path(temp_dir) / 'cadfs'
            json_path.write_text(
                json.dumps([{'url': 'https://example.test/one'}, {'url': 'https://example.test/two'}]),
                encoding='utf-8',
            )

            with patch('src.cadfs._convert_onshape_link', side_effect=self.fake_convert):
                results = batch_onshape_links_to_cadfs(json_path, output_dir)

            self.assertEqual([result.name for result in results], ['00000000', '00000001'])
            self.assertTrue(all(result.status == 'success' for result in results))

    def test_rejects_object_without_url_key_before_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / 'urls.json'
            json_path.write_text(json.dumps([{'link': 'https://example.test/one'}]), encoding='utf-8')
            output = StringIO()

            with redirect_stdout(output), self.assertRaises(ValueError):
                batch_onshape_links_to_cadfs(json_path, Path(temp_dir) / 'cadfs')

            self.assertIn('used 0 Onshape request(s)', output.getvalue())

    def test_overwrite_removes_stale_file_when_conversion_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / 'urls.json'
            output_dir = Path(temp_dir) / 'cadfs'
            output_dir.mkdir()
            destination = output_dir / '00000000.txt'
            destination.write_text('stale CADFS', encoding='utf-8')
            json_path.write_text(json.dumps(['https://example.test/one']), encoding='utf-8')

            with patch('src.cadfs._convert_onshape_link', side_effect=RuntimeError('conversion failed')):
                results = batch_onshape_links_to_cadfs(json_path, output_dir, overwrite=True)

            self.assertEqual(results[0].status, 'failed')
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
