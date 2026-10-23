from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from resume_pdf import A4, BOTTOM, HEIGHT, TOOL_ROOT, build_resume, parse_source, register_fonts


class ResumePdfTests(unittest.TestCase):
    def test_parser_handles_frontmatter_bom_and_indented_continuations(self):
        source = '\ufeff---\r\ntags: [example]\r\n---\r\n# Example\r\nRole\r\nContact\r\n## Skills\r\n- First\r\n  continuation\r\n'
        document = parse_source(source)
        self.assertEqual(document[:3], ('Example', 'Role', 'Contact'))
        self.assertEqual(document[-1], [('Skills', ['- First continuation'])])

    def test_invalid_source_has_a_readable_error(self):
        for source in ('', 'Name\nRole', '# Name\nRole\nContact', '# Name\nRole\nContact\n## Empty'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_source(source)

    def test_fictional_example_has_one_page_clickable_links_and_safe_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'resume.pdf'
            manifest = build_resume(TOOL_ROOT / 'examples/resume.md', output)
            reader = PdfReader(output)
            self.assertEqual(len(reader.pages), 1)
            self.assertAlmostEqual(float(reader.pages[0].mediabox.width), A4[0], places=3)
            self.assertAlmostEqual(float(reader.pages[0].mediabox.height), A4[1], places=3)
            text = reader.pages[0].extract_text()
            self.assertIn('candidate@example.com', text)
            self.assertIn('林知远', text)
            self.assertEqual(reader.metadata.author, '')
            self.assertEqual(reader.metadata.creator, 'resume-pdf-template')
            links = {annotation.get_object().get('/A', {}).get('/URI')
                     for annotation in reader.pages[0].get('/Annots', [])}
            self.assertIn('https://example.com/portfolio', links)
            self.assertIn('https://example.org/demo', links)
            self.assertLessEqual(manifest['last_content_bottom_pt'], HEIGHT - BOTTOM)
            self.assertEqual(manifest['source'], 'resume.md')
            for item in manifest['layout']:
                self.assertLessEqual(item['bottom'], HEIGHT - BOTTOM)
                for line in item['lines']:
                    self.assertLessEqual(line['width'], item['width'])

    def test_generation_is_deterministic_for_the_same_input_and_fonts(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first.pdf', Path(directory) / 'second.pdf'
            build_resume(TOOL_ROOT / 'examples/resume.md', first)
            build_resume(TOOL_ROOT / 'examples/resume.md', second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_cid_font_fallback_generates_a_readable_single_page(self):
        with patch('resume_pdf.Path.is_file', return_value=False):
            fonts = register_fonts()
        self.assertEqual(fonts, ('STSong-Light', 'STSong-Light'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'fallback.pdf'
            with patch('resume_pdf.register_fonts', return_value=fonts):
                build_resume(TOOL_ROOT / 'examples/resume.md', output)
            reader = PdfReader(output)
            self.assertEqual(len(reader.pages), 1)
            self.assertIn('林知远', reader.pages[0].extract_text())

    def test_overflow_does_not_replace_an_existing_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'long.md', Path(directory) / 'existing.pdf'
            source.write_text('# Example\nRole\nContact\n## Skills\n' + '- 示例内容\n' * 200, encoding='utf-8')
            output.write_bytes(b'original content')
            with self.assertRaisesRegex(ValueError, 'minimum 8.2 pt'):
                build_resume(source, output)
            self.assertEqual(output.read_bytes(), b'original content')

    def test_source_and_output_must_be_different(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.md'
            with self.assertRaisesRegex(ValueError, 'different paths'):
                build_resume(source, source)

    def test_import_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c',
                                     f'import sys; sys.path.insert(0, {str(TOOL_ROOT)!r}); import resume_pdf'],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
