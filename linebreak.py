"""Measured CJK/Latin line breaking independent of any resume content."""

from dataclasses import dataclass, fields
from html import escape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re

from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Paragraph


@dataclass(frozen=True)
class Rules:
    schema_version: int = 1
    keep_together: tuple = ()
    units: tuple = ('毫秒', '分钟', '小时', '万条', '千条', '千家', '型号', '个月', '万元',
                    '年', '月', '天', '秒', '次', '组', '条', '个', '家', '万', 'GB', 'MB', 'ms', '%', '％')
    opening_punctuation: str = '（【《「『〔〈“‘([{'
    closing_punctuation: str = '，。！？；：、）】》」』〕〉”’％%…,.;:!?)]}'
    width_safety_pt: float = 0.25
    last_line_min_ratio: float = 0.25
    last_line_min_chars: float = 12
    previous_line_min_ratio: float = 0.6

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('schema_version must be 1')
        for name in ('keep_together', 'units'):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(term, str) or not term.strip() or '\n' in term
                for term in value
            ):
                raise ValueError(f'{name} must be a list of nonempty single-line strings')
            object.__setattr__(self, name, tuple(dict.fromkeys(value)))
        for name in ('opening_punctuation', 'closing_punctuation'):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f'{name} must be a string')
        for name in ('width_safety_pt', 'last_line_min_ratio',
                     'last_line_min_chars', 'previous_line_min_ratio'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number')
        if self.width_safety_pt < 0 or self.last_line_min_chars < 0:
            raise ValueError('Width safety and minimum characters must be nonnegative')
        for name in ('last_line_min_ratio', 'previous_line_min_ratio'):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f'{name} must be between 0 and 1')

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Rules must be a JSON object')
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown rule keys: {sorted(unknown)}')
        return cls(**data)


def _bold_markup(text):
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escape(text))


def markup(text):
    """Escape arbitrary input, allowing only bold and HTTP(S) Markdown links."""
    parts = []
    offset = 0
    for match in re.finditer(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', text):
        parts.append(_bold_markup(text[offset:match.start()]))
        parts.append('<link href="' + escape(match[2], quote=True) +
                     '" color="#174a73">' + _bold_markup(match[1]) + '</link>')
        offset = match.end()
    parts.append(_bold_markup(text[offset:]))
    return ''.join(parts).replace('\n', '<br/>')


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    href: str | None = None


class RichTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bold_depth = 0
        self.href = None
        self.runs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'b':
            self.bold_depth += 1
        elif tag == 'link':
            self.href = dict(attrs)['href']
        elif tag == 'br':
            self.runs.append(Run('\n', self.bold_depth > 0, self.href))

    def handle_endtag(self, tag):
        if tag == 'b':
            self.bold_depth -= 1
        elif tag == 'link':
            self.href = None

    def handle_data(self, data):
        self.runs.append(Run(data, self.bold_depth > 0, self.href))


def parse_runs(text):
    parser = RichTextParser()
    parser.feed(markup(text))
    parser.close()
    return parser.runs


def plain_text(text):
    return ''.join(run.text for run in parse_runs(text))


@dataclass(frozen=True)
class Token:
    runs: tuple
    width: float
    index: int

    @property
    def text(self):
        return ''.join(run.text for run in self.runs)


def _pattern(rules):
    terms = sorted(rules.keep_together, key=len, reverse=True)
    units = '|'.join(re.escape(unit) for unit in sorted(rules.units, key=len, reverse=True))
    numeric = r'\d+(?:[,.]\d+)*(?:[ \t]*[-~至][ \t]*\d+(?:[,.]\d+)*)?\+?'
    if units:
        numeric += r'(?:[ \t]*(?:' + units + r'))?'
    alternatives = [re.escape(term) for term in terms]
    alternatives += [r'\n', numeric, r'https?://[^\s，。；！？、（）【】<>]+',
                     r'[A-Za-z][A-Za-z0-9_]*(?:[./@:#%=&?~+\-][A-Za-z0-9_]+)*[+#]*',
                     r'[^\S\n]+', r'.']
    return re.compile('|'.join(alternatives))


def tokenize(text, paragraph_style, rules, bold_font):
    characters = []
    for run in parse_runs(text):
        characters.extend(Run(char, run.bold, run.href) for char in run.text)
    visible = ''.join(char.text for char in characters)
    tokens = []
    for match in _pattern(rules).finditer(visible):
        runs = []
        for char in characters[match.start():match.end()]:
            if runs and (runs[-1].bold, runs[-1].href) == (char.bold, char.href):
                last = runs[-1]
                runs[-1] = Run(last.text + char.text, char.bold, char.href)
            else:
                runs.append(char)
        width = sum(pdfmetrics.stringWidth(run.text, bold_font if run.bold else paragraph_style.fontName,
                                           paragraph_style.fontSize)
                    for run in runs if run.text != '\n')
        tokens.append(Token(tuple(runs), width, len(tokens)))
    return tokens


def trim_tokens(tokens):
    start, end = 0, len(tokens)
    while start < end and tokens[start].text.isspace():
        start += 1
    while end > start and tokens[end - 1].text.isspace():
        end -= 1
    return tokens[start:end]


def allowed_break(tokens, index, rules):
    before, after = index - 1, index
    while before >= 0 and tokens[before].text.isspace():
        before -= 1
    while after < len(tokens) and tokens[after].text.isspace():
        after += 1
    return (before < 0 or tokens[before].text[-1] not in rules.opening_punctuation) and (
        after >= len(tokens) or tokens[after].text[0] not in rules.closing_punctuation)


def _width(tokens):
    return sum(token.width for token in tokens)


def _wrap_segment(tokens, limit, style, rules):
    lines = []
    remaining = trim_tokens(tokens)
    while remaining:
        end, used = 0, 0
        while end < len(remaining) and used + remaining[end].width <= limit:
            used += remaining[end].width
            end += 1
        while end and end < len(remaining) and not allowed_break(remaining, end, rules):
            end -= 1
        if not end:
            raise ValueError('Unbreakable text or punctuation group exceeds available width: ' +
                             repr(remaining[0].text) + '. Shorten it, use a short link label, '
                             'increase the column width, or revise keep_together.')
        lines.append(trim_tokens(remaining[:end]))
        remaining = trim_tokens(remaining[end:])
    minimum = min(limit * rules.last_line_min_ratio, style.fontSize * rules.last_line_min_chars)
    if len(lines) > 1 and _width(lines[-1]) < minimum:
        combined = [token for token in tokens
                    if lines[-2][0].index <= token.index <= lines[-1][-1].index]
        for end in range(len(lines[-2]) - 1, 0, -1):
            if not allowed_break(combined, end, rules):
                continue
            first, last = trim_tokens(combined[:end]), trim_tokens(combined[end:])
            if (first and minimum <= _width(last) <= limit and
                    _width(first) >= limit * rules.previous_line_min_ratio):
                lines[-2:] = [first, last]
                break
    return lines


def wrap_lines(text, paragraph_style, width, rules, bold_font):
    if not math.isfinite(width) or width <= rules.width_safety_pt:
        raise ValueError('Available width must exceed width_safety_pt')
    segments = [[]]
    for token in tokenize(text, paragraph_style, rules, bold_font):
        if token.text == '\n':
            segments.append([])
        else:
            segments[-1].append(token)
    lines = []
    for segment in segments:
        lines.extend(_wrap_segment(segment, width - rules.width_safety_pt, paragraph_style, rules) or [[]])
    return lines


def format_tokens(tokens):
    parts = []
    for token in tokens:
        for run in token.runs:
            part = escape(run.text)
            if run.bold:
                part = '<b>' + part + '</b>'
            if run.href:
                part = '<link href="' + escape(run.href, quote=True) + '" color="#174a73">' + part + '</link>'
            parts.append(part)
    return ''.join(parts)


def wrapped_paragraph(text, paragraph_style, width, rules=None, bold_font='Helvetica-Bold'):
    rules = rules or Rules()
    lines = wrap_lines(text, paragraph_style, width, rules, bold_font)
    paragraph = Paragraph('<br/>'.join(format_tokens(line) for line in lines), paragraph_style)
    _, height = paragraph.wrap(width, 100000)
    if len(paragraph.blPara.lines) != len(lines):
        raise ValueError('The PDF renderer changed an explicitly measured line break: ' + plain_text(text))
    info = [{'text': ''.join(token.text for token in line), 'width': _width(line)} for line in lines]
    return paragraph, height, info
