import json
from pathlib import Path
import tempfile
import unittest

from reportlab.lib.styles import ParagraphStyle

from linebreak import Rules, allowed_break, markup, plain_text, tokenize, wrap_lines, wrapped_paragraph
from resume_pdf import register_fonts


class LineBreakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regular, cls.bold = register_fonts()
        cls.style = ParagraphStyle('test', fontName=cls.regular, fontSize=10,
                                   leading=14, splitLongWords=False, spaceShrinkage=0)

    def tokens(self, text, rules=None):
        return tokenize(text, self.style, rules or Rules(), self.bold)

    def lines(self, text, width=120, rules=None):
        return wrap_lines(text, self.style, width, rules or Rules(), self.bold)

    def assert_valid_lines(self, lines, width, rules=None):
        rules = rules or Rules()
        for index, line in enumerate(lines):
            visible = ''.join(token.text for token in line)
            self.assertLessEqual(sum(token.width for token in line), width - rules.width_safety_pt)
            self.assertEqual(visible.strip(), visible)
            if index:
                self.assertNotIn(visible[0], rules.closing_punctuation)
            if index < len(lines) - 1:
                self.assertNotIn(visible[-1], rules.opening_punctuation)

    def test_chinese_punctuation_and_width_at_many_column_sizes(self):
        text = '负责接口设计（包含权限、日志与校验），维护《使用说明》，完成测试。继续改进交付流程！'
        for width in (65, 81, 100, 135, 200):
            with self.subTest(width=width):
                lines = self.lines(text, width)
                self.assert_valid_lines(lines, width)
                self.assertEqual(''.join(token.text for line in lines for token in line), text)

    def test_english_words_are_atomic_but_sentences_can_wrap(self):
        tokens = self.tokens('Python TypeScript CI/CD Node.js C++')
        self.assertEqual([token.text for token in tokens if not token.text.isspace()],
                         ['Python', 'TypeScript', 'CI/CD', 'Node.js', 'C++'])
        lines = self.lines('Python TypeScript Python TypeScript', 100)
        self.assertGreater(len(lines), 1)
        self.assertEqual(' '.join(token.text for line in lines for token in line).split(),
                         ['Python', 'TypeScript', 'Python', 'TypeScript'])

    def test_number_units_and_ranges_are_atomic(self):
        tokens = self.tokens('耗时 12.5 ms，覆盖 10 万条，增长 25%，100+ 组，周期 2024.03 - 2025.09')
        visible = [token.text for token in tokens]
        for expected in ('12.5 ms', '10 万条', '25%', '100+ 组', '2024.03 - 2025.09'):
            self.assertIn(expected, visible)

    def test_configured_terms_use_longest_match(self):
        rules = Rules(keep_together=('持续', '持续集成', 'Docker 镜像'))
        tokens = self.tokens('持续集成与 Docker 镜像', rules)
        self.assertEqual([token.text for token in tokens if not token.text.isspace()],
                         ['持续集成', '与', 'Docker 镜像'])

    def test_formatting_does_not_create_an_english_break(self):
        tokens = self.tokens('**Type**Script')
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].text, 'TypeScript')
        self.assertEqual([run.bold for run in tokens[0].runs], [True, False])

    def test_spaces_after_bold_are_preserved(self):
        self.assertEqual(plain_text('**Python** services'), 'Python services')
        self.assertIn('</b> services', markup('**Python** services'))

    def test_links_survive_wraps_and_query_escaping(self):
        text = '[接口设计与维护文档](https://example.com/docs?a=1&b=2)'
        paragraph, _, info = wrapped_paragraph(text, self.style, 55, Rules(), self.bold)
        self.assertGreater(len(info), 1)
        self.assertEqual(''.join(line['text'] for line in info), '接口设计与维护文档')
        self.assertIn('a=1&amp;b=2', paragraph.text)
        self.assertEqual({run.href for token in self.tokens(text) for run in token.runs},
                         {'https://example.com/docs?a=1&b=2'})

    def test_raw_markup_and_non_http_links_are_literal(self):
        text = '<img src="private"> [file](file:///private) & text'
        self.assertEqual(plain_text(text), text)
        self.assertIn('&lt;img', markup(text))
        self.assertNotIn('<link', markup(text))

    def test_oversized_token_fails_with_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'Shorten it'):
            self.lines('Supercalifragilisticexpialidocious', 15)

    def test_punctuation_can_require_more_than_one_token_to_fit(self):
        with self.assertRaisesRegex(ValueError, 'punctuation group'):
            self.lines('（测）', 15)

    def test_short_last_line_rebalances_without_losing_internal_spaces(self):
        text = 'Python APIs improve testing and delivery for teams'
        rules = Rules(last_line_min_ratio=0.4, last_line_min_chars=12, previous_line_min_ratio=0.4)
        for width in (125, 140, 160):
            with self.subTest(width=width):
                lines = self.lines(text, width, rules)
                self.assert_valid_lines(lines, width, rules)
                normalized = ' '.join(''.join(token.text for token in line) for line in lines)
                self.assertEqual(normalized, text)

    def test_short_last_line_is_improved_when_a_legal_break_exists(self):
        text = '数' * 11
        lines = self.lines(text, 100, Rules(last_line_min_ratio=0.3))
        self.assertEqual([len(''.join(token.text for token in line)) for line in lines], [8, 3])

    def test_explicit_newline_is_preserved(self):
        paragraph, _, info = wrapped_paragraph('第一行\nSecond line', self.style, 150, Rules(), self.bold)
        self.assertEqual([line['text'] for line in info], ['第一行', 'Second line'])
        self.assertEqual(len(paragraph.blPara.lines), 2)

    def test_whitespace_is_ignored_at_punctuation_boundary(self):
        tokens = self.tokens('前文 （ 后文 ）')
        opener = next(index for index, token in enumerate(tokens) if token.text == '（')
        closer = next(index for index, token in enumerate(tokens) if token.text == '）')
        self.assertFalse(allowed_break(tokens, opener + 1, Rules()))
        self.assertFalse(allowed_break(tokens, closer, Rules()))

    def test_invalid_width_is_rejected(self):
        for width in (0, -1, 0.1, float('nan'), float('inf')):
            with self.subTest(width=width), self.assertRaises(ValueError):
                self.lines('text', width)

    def test_invalid_rules_are_rejected(self):
        invalid = ({'schema_version': 2}, {'schema_version': True}, {'keep_together': ['']},
                   {'units': 'ms'}, {'units': [None]}, {'width_safety_pt': -1},
                   {'last_line_min_ratio': 1.1}, {'previous_line_min_ratio': True},
                   {'width_safety_pt': float('nan')})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Rules(**values)

    def test_unknown_rules_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.json'
            path.write_text(json.dumps({'unknown': 'key'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unknown rule keys'):
                Rules.load(path)


if __name__ == '__main__':
    unittest.main()
