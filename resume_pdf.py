"""Generate a measured, single-page PDF from a small Markdown resume format."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from linebreak import Rules, plain_text, wrapped_paragraph


TOOL_ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = A4
LEFT = RIGHT = 14 * mm
TOP = BOTTOM = 11 * mm
CONTENT_WIDTH = WIDTH - LEFT - RIGHT
INK = colors.HexColor('#27313b')
HEADING = colors.HexColor('#1f2933')
BLUE = colors.HexColor('#174a73')
MUTED = colors.HexColor('#5e6b76')
RULE = colors.HexColor('#d7e1e8')
FONT_ATTEMPTS = ((9.2, 13), (9.0, 12.5), (8.8, 12), (8.5, 11.5),
                 (8.4, 11.2), (8.3, 11.0), (8.2, 10.8))


def register_fonts(font=None, bold_font=None):
    if bold_font and not font:
        raise ValueError('--bold-font requires --font')
    if not font:
        candidates = (
            (Path('C:/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/msyhbd.ttc')),
            (Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'), None),
        )
        for regular, bold in candidates:
            if regular.is_file():
                font, bold_font = regular, bold if bold and bold.is_file() else regular
                break
    if font:
        pdfmetrics.registerFont(TTFont('ResumeRegular', str(font), subfontIndex=0))
        pdfmetrics.registerFont(TTFont('ResumeBold', str(bold_font or font), subfontIndex=0))
        regular, bold = 'ResumeRegular', 'ResumeBold'
    else:
        # Standard CJK CID font; the PDF viewer supplies the actual font.
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        regular = bold = 'STSong-Light'
    pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold,
                                  italic=regular, boldItalic=bold)
    return regular, bold


def parse_source(raw):
    raw = raw.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    body = re.sub(r'\A---[^\S\n]*\n.*?\n---[^\S\n]*(?:\n|$)', '', raw, count=1, flags=re.S)
    header, sections = [], []
    for raw_line in body.splitlines():
        if not raw_line.strip():
            continue
        line = raw_line.strip()
        if line.startswith('## '):
            sections.append((line[3:], []))
        elif sections:
            content = sections[-1][1]
            if raw_line.startswith(('  ', '\t')) and content:
                content[-1] += ' ' + line
            else:
                content.append(line)
        else:
            header.append(line)
    if len(header) < 3 or not header[0].startswith('# '):
        raise ValueError('Expected # name, role, and contact lines before the first ## section')
    if not sections or any(not title or not content for title, content in sections):
        raise ValueError('At least one nonempty ## section is required')
    name, role, contact, *links = header
    return name[2:], role.strip('*'), contact, links, sections


def compose(document, body_size, leading, rules, fonts):
    regular, bold_font = fonts

    def style(name, size, line_height, bold=False, color=INK, **kwargs):
        return ParagraphStyle(name, fontName=bold_font if bold else regular,
                              fontSize=size, leading=line_height, textColor=color,
                              splitLongWords=False, spaceShrinkage=0, **kwargs)

    styles = {
        'name': style('name', 22, 26, bold=True, color=HEADING),
        'role': style('role', 10, 13, bold=True, color=BLUE),
        'contact': style('contact', 8.1, 11, color=MUTED),
        'body': style('body', body_size, leading),
        'section': style('section', 10.5, 13, bold=True, color=BLUE),
        'entry': style('entry', 9.3, 12, bold=True, color=HEADING),
        'meta': style('meta', 7.9, 11, color=MUTED, alignment=TA_RIGHT),
        'project_meta': style('project_meta', 8.0, 11, color=MUTED),
    }
    blocks = []

    def gap(height):
        blocks.append({'type': 'gap', 'height': height})

    def measure(text, sty, width):
        return wrapped_paragraph(text, styles[sty], width, rules, bold_font)

    def paragraph(text, sty='body', bullet=False):
        indent = 9 if bullet else 0
        p, height, lines = measure(text, sty, CONTENT_WIDTH - indent)
        blocks.append({'type': 'paragraph', 'paragraph': p, 'height': height,
                       'indent': indent, 'bullet': bullet, 'style': sty,
                       'text': plain_text(text), 'lines': lines, 'width': CONTENT_WIDTH - indent})

    def entry_heading(title, meta=None):
        if meta is None:
            paragraph(title, 'entry')
            return
        right_width = min(CONTENT_WIDTH * 0.42, max(110, pdfmetrics.stringWidth(
            plain_text(meta), regular, styles['meta'].fontSize) + 4))
        left_width = CONTENT_WIDTH - right_width - 8
        left_p, left_h, left_lines = measure(title, 'entry', left_width)
        right_p, right_h, right_lines = measure(meta, 'meta', right_width)
        blocks.append({'type': 'row', 'height': max(left_h, right_h), 'parts': [
            (left_p, 0, left_h, plain_text(title), left_lines, left_width),
            (right_p, CONTENT_WIDTH - right_width, right_h, plain_text(meta), right_lines, right_width),
        ]})

    name, role, contact, links, sections = document
    paragraph(name, 'name')
    gap(1)
    paragraph(role, 'role')
    gap(2)
    paragraph(' · '.join([contact, *links]), 'contact')

    for title, content in sections:
        gap(7)
        paragraph(title, 'section')
        gap(1)
        blocks.append({'type': 'rule', 'height': 0.6})
        gap(4)
        index = 0
        while index < len(content):
            line = content[index]
            if line.startswith('### '):
                if index:
                    gap(5)
                meta = None
                if index + 1 < len(content):
                    following = content[index + 1]
                    if following.startswith('**项目时间：**'):
                        meta = following
                        index += 1
                    elif following.startswith('**') and following.endswith('**'):
                        meta = following[2:-2]
                        index += 1
                entry_heading(line[4:].replace('｜', ' | '), meta)
                gap(2)
            elif line.startswith('- '):
                paragraph(line[2:], bullet=True)
                if index + 1 < len(content) and content[index + 1].startswith('- '):
                    gap(2.5)
            elif line.startswith('**技术栈：**'):
                paragraph(line, 'project_meta')
                gap(2)
            else:
                paragraph(line)
            index += 1
    return blocks, styles


def build_resume(source, output, rules_path=None, manifest_path=None, font=None, bold_font=None):
    source, output = Path(source), Path(output)
    manifest_path = Path(manifest_path) if manifest_path else None
    paths = [source.resolve(), output.resolve()]
    if manifest_path:
        paths.append(manifest_path.resolve())
    if len(paths) != len(set(paths)):
        raise ValueError('Source, PDF output, and manifest must use different paths')
    source_bytes = source.read_bytes()
    document = parse_source(source_bytes.decode('utf-8-sig'))
    rules = Rules.load(rules_path or TOOL_ROOT / 'linebreak-rules.json')
    fonts = register_fonts(font, bold_font)
    attempts = []
    for size, leading in FONT_ATTEMPTS:
        blocks, styles = compose(document, size, leading, rules, fonts)
        total = sum(block['height'] for block in blocks)
        attempts.append({'body_font_pt': size, 'leading_pt': leading, 'height_pt': total})
        if total <= HEIGHT - TOP - BOTTOM:
            break
    else:
        raise ValueError('Content cannot fit one A4 page at the minimum 8.2 pt font size; shorten the source')

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=A4, pageCompression=1, invariant=1)
    pdf.setTitle(plain_text(document[0]) + ' - ' + plain_text(document[1]))
    pdf.setAuthor('')
    pdf.setSubject('Markdown resume')
    pdf.setCreator('resume-pdf-template')
    pdf.setFillColor(BLUE)
    pdf.rect(0, HEIGHT - 3 * mm, WIDTH, 3 * mm, fill=1, stroke=0)
    y, layout = HEIGHT - TOP, []
    for block in blocks:
        if block['type'] == 'paragraph':
            block['paragraph'].drawOn(pdf, LEFT + block['indent'], y - block['height'])
            if block['bullet']:
                pdf.setFillColor(INK)
                pdf.circle(LEFT + 2.3, y - styles[block['style']].fontSize + 2.1, 0.8, fill=1, stroke=0)
            layout.append({'text': block['text'], 'top': HEIGHT - y,
                           'bottom': HEIGHT - y + block['height'], 'style': block['style'],
                           'lines': block['lines'], 'width': block['width']})
        elif block['type'] == 'row':
            for p, offset, height, text, lines, width in block['parts']:
                p.drawOn(pdf, LEFT + offset, y - height)
                layout.append({'text': text, 'top': HEIGHT - y, 'bottom': HEIGHT - y + height,
                               'style': 'entry_row', 'lines': lines, 'width': width})
        elif block['type'] == 'rule':
            pdf.setStrokeColor(RULE)
            pdf.setLineWidth(0.6)
            pdf.line(LEFT, y - 0.3, WIDTH - RIGHT, y - 0.3)
        y -= block['height']
    pdf.showPage()
    pdf.save()
    manifest = {'rules_schema_version': rules.schema_version, 'source': source.name,
                'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                'output': output.name, 'pages': 1, 'page_size': A4,
                'body_font_pt': size, 'body_leading_pt': leading,
                'last_content_bottom_pt': HEIGHT - y, 'attempts': attempts, 'layout': layout}
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path, default=TOOL_ROOT / 'examples/resume.md')
    parser.add_argument('-o', '--output', type=Path, default=TOOL_ROOT / 'output/pdf/resume.pdf')
    parser.add_argument('--rules', type=Path, default=TOOL_ROOT / 'linebreak-rules.json')
    parser.add_argument('--manifest', type=Path, help='Optional QA JSON; contains source text')
    parser.add_argument('--font', type=Path, help='TrueType font or TrueType-outline TTC (index 0)')
    parser.add_argument('--bold-font', type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_resume(args.source, args.output, args.rules, args.manifest, args.font, args.bold_font)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Generation failed: {error}\n')
    print(json.dumps({'output': str(args.output.resolve()), 'pages': manifest['pages'],
                      'body_font_pt': manifest['body_font_pt']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
