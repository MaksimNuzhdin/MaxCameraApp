#!/usr/bin/env python3
"""Build the public English user guide without accessing camera or network.

Dependencies: reportlab, Pillow; DejaVu Sans (regular/bold) and Sans Mono fonts.
Supported source: headings, paragraphs, numbered/bulleted lists, fenced code,
Markdown tables/links/images, <!-- toc --> blocks, and <!-- pagebreak -->.
Run from any directory. Default input/output are alongside this script.
"""
from __future__ import annotations

import argparse
import html
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Preformatted, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent
INK = colors.HexColor('#162d3c')
MUTED = colors.HexColor('#526675')
ACCENT = colors.HexColor('#007c86')
LIGHT = colors.HexColor('#edf5f6')
BORDER = colors.HexColor('#d8e2e7')
PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_T, MARGIN_B = 18 * mm, 20 * mm, 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X


def register_fonts(font_dir=None):
    candidates = [Path(font_dir)] if font_dir else []
    env = os.environ.get('MAXCAMERA_GUIDE_FONT_DIR')
    if env:
        candidates.append(Path(env))
    candidates += [Path('/usr/share/fonts/truetype/dejavu'),
                   Path('/usr/local/share/fonts'), Path.home() / '.fonts']
    required = {'Guide': 'DejaVuSans.ttf', 'GuideBold': 'DejaVuSans-Bold.ttf',
                'GuideMono': 'DejaVuSansMono.ttf'}
    for family, filename in required.items():
        match = next((p / filename for p in candidates if (p / filename).is_file()), None)
        if match is None:
            raise SystemExit(f'Missing {filename}; use --font-dir or MAXCAMERA_GUIDE_FONT_DIR')
        pdfmetrics.registerFont(TTFont(family, str(match)))
    pdfmetrics.registerFontFamily('Guide', normal='Guide', bold='GuideBold',
                                 italic='Guide', boldItalic='GuideBold')


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r'`([^`]+)`', r'<font name="GuideMono" size="8.2">\1</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',
                  r'<link href="\2" color="#007c86">\1</link>', text)
    return text


def styles():
    base = dict(fontName='Guide', textColor=INK, fontSize=9.3, leading=13.2,
                spaceAfter=6, allowWidows=0, allowOrphans=0)
    s = {
        'body': ParagraphStyle('Body', **base),
        'h1': ParagraphStyle('H1', fontName='GuideBold', fontSize=20, leading=25,
                             textColor=INK, spaceBefore=0, spaceAfter=15, keepWithNext=True),
        'h2': ParagraphStyle('H2', fontName='GuideBold', fontSize=12.4, leading=17,
                             textColor=ACCENT, spaceBefore=10, spaceAfter=7, keepWithNext=True),
        'cover': ParagraphStyle('Cover', fontName='GuideBold', fontSize=35, leading=42,
                                textColor=INK, spaceBefore=20, spaceAfter=22),
        'subtitle': ParagraphStyle('Subtitle', fontName='Guide', fontSize=20, leading=27,
                                   textColor=ACCENT, spaceAfter=22),
        'bullet': ParagraphStyle('Bullet', **{**base, 'leftIndent': 12,
                                  'firstLineIndent': -10, 'spaceAfter': 5}),
        'caption': ParagraphStyle('Caption', fontName='Guide', fontSize=8, leading=11,
                                  textColor=MUTED, spaceBefore=4, spaceAfter=8,
                                  alignment=TA_CENTER),
        'table': ParagraphStyle('Cell', fontName='Guide', fontSize=8.4, leading=11.8,
                                textColor=INK, spaceAfter=0),
        'tablehead': ParagraphStyle('CellHead', fontName='GuideBold', fontSize=8.4,
                                    leading=11.8, textColor=colors.white, spaceAfter=0),
        'code': ParagraphStyle('Code', fontName='GuideMono', fontSize=7.8, leading=11,
                               textColor=INK, backColor=LIGHT, borderPadding=8,
                               spaceBefore=6, spaceAfter=10),
    }
    return s


class GuideDoc(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(str(filename), pagesize=A4, leftMargin=MARGIN_X,
                         rightMargin=MARGIN_X, topMargin=MARGIN_T,
                         bottomMargin=MARGIN_B, title='MaxCameraApp - User Guide',
                         author='MaxCameraApp', subject='English User and Administrator Guide',
                         **kwargs)
        frame = Frame(MARGIN_X, MARGIN_B, CONTENT_W, PAGE_H - MARGIN_T - MARGIN_B,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='guide', frames=[frame], onPage=self.decorate))
        self.heading_index = 0

    def beforeDocument(self):
        self.heading_index = 0

    def decorate(self, c, doc):
        c.saveState()
        c.setCreator('MaxCameraApp guide builder')
        if doc.page == 1:
            # Small product-inspired Bayer motif; no scene or private camera data.
            for row in range(2):
                for col in range(2):
                    c.setFillColor([ACCENT, colors.HexColor('#f2b63f'),
                                    colors.HexColor('#45aa91'), INK][row * 2 + col])
                    c.roundRect(PAGE_W - 45*mm + col*9*mm,
                                PAGE_H - 30*mm - row*9*mm, 7*mm, 7*mm, 1.4*mm,
                                fill=1, stroke=0)
        else:
            c.setFont('Guide', 7.4)
            c.setFillColor(MUTED)
            c.drawString(MARGIN_X, PAGE_H - 11.5*mm, 'MaxCameraApp  /  User Guide')
            c.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 11.5*mm, 'EN · 6 Nov 2026')
        c.setFont('Guide', 7.5)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_X, 10*mm, 'MaxCamera Control')
        c.drawRightString(PAGE_W - MARGIN_X, 10*mm, str(doc.page))
        c.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == 'H1':
            heading = flowable.getPlainText()
            if re.match(r'^\d+\.', heading):
                self.heading_index += 1
                key = 'chapter-%02d' % self.heading_index
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(heading, key, 0, False)
                self.notify('TOCEntry', (0, heading, self.page, key))


def table_flow(rows, s):
    n = len(rows[0])
    if n == 2:
        # First column is a label; give the explanation most of the measure.
        widths = [CONTENT_W*.34, CONTENT_W*.66]
    elif n == 4:
        widths = [CONTENT_W*.25, CONTENT_W*.30, CONTENT_W*.21, CONTENT_W*.24]
    else:
        widths = [CONTENT_W/n]*n
    data = [[Paragraph(inline(cell), s['tablehead' if i == 0 else 'table'])
             for cell in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), ACCENT),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT]),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LINEBELOW', (0,0), (-1,0), .4, ACCENT),
        ('LINEBELOW', (0,1), (-1,-1), .3, BORDER),
    ]))
    return [t, Spacer(1, 8)]


def parse_source(source, s):
    lines = source.read_text(encoding='utf-8').splitlines()
    story, i, cover = [], 0, True
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line == '<!-- pagebreak -->':
            story.append(PageBreak())
            cover = False
            i += 1
            continue
        if line in ('{{TOC}}', '<!-- toc -->'):
            toc = TableOfContents()
            toc.levelStyles = [ParagraphStyle('TOC', fontName='Guide', fontSize=10,
                                leading=12.5, textColor=INK, leftIndent=0,
                                firstLineIndent=0, rightIndent=20, spaceBefore=0)]
            toc.dotsMinLevel = 0
            toc.tableStyle = TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'),
                                         ('LEFTPADDING', (0,0), (-1,-1), 0),
                                         ('RIGHTPADDING', (0,0), (-1,-1), 0),
                                         ('TOPPADDING', (0,0), (-1,-1), 2),
                                         ('BOTTOMPADDING', (0,0), (-1,-1), 2)])
            story.append(toc)
            if line == '<!-- toc -->':
                while i < len(lines) and lines[i].strip() != '<!-- /toc -->':
                    i += 1
            i += 1
            continue
        if line.startswith('# '):
            story.append(Paragraph(inline(line[2:]), s['cover' if cover else 'h1']))
            i += 1
            continue
        if line.startswith('## '):
            story.append(Paragraph(inline(line[3:]), s['subtitle' if cover else 'h2']))
            i += 1
            continue
        if line.startswith('```'):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                if len(lines[i]) > 98:
                    raise ValueError(f'Code line too long at {i+1}: wrap explicitly with shell continuation')
                block.append(lines[i])
                i += 1
            story.append(Preformatted('\n'.join(block), s['code']))
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', c) for c in cells):
                    rows.append(cells)
                i += 1
            story.extend(table_flow(rows, s))
            continue
        img = re.fullmatch(r'!\[([^]]*)\]\(([^)]+)\)(?:\{width=(\d+(?:\.\d+)?)\})?', line)
        if img:
            caption, rel, width = img.groups()
            path = (source.parent / rel).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            from PIL import Image as PILImage
            with PILImage.open(path) as im:
                w, h = im.size
            width_pt = min(float(width or (49 if 'mobile' in path.stem else 169))*mm, CONTENT_W)
            graphic = Image(str(path), width=width_pt, height=width_pt*h/w)
            graphic.hAlign = 'CENTER'
            story.append(KeepTogether([graphic, Paragraph(inline(caption), s['caption'])]))
            i += 1
            continue
        bullet = re.match(r'^(?:- |(\d+)\. )(.*)', line)
        if bullet:
            number, body = bullet.groups()
            prefix = f'{number}.' if number else '•'
            story.append(Paragraph(inline(prefix + ' ' + body), s['bullet']))
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#|<!--|\||```|!\[|- |\d+\. )', lines[i]):
            para.append(lines[i].strip())
            i += 1
        story.append(Paragraph(inline(' '.join(para)), s['body']))
    return story


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'MaxCameraApp_User_Guide.md')
    parser.add_argument('--output', type=Path, default=ROOT/'MaxCameraApp_User_Guide.pdf')
    parser.add_argument('--font-dir')
    args = parser.parse_args()
    register_fonts(args.font_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Stable public release timestamp unless a caller supplies another build epoch.
    os.environ.setdefault('SOURCE_DATE_EPOCH', '1793923200')
    doc = GuideDoc(args.output)
    doc.multiBuild(parse_source(args.source.resolve(), styles()))
    print(f'Created {args.output} ({args.output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
