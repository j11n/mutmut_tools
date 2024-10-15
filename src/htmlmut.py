#!/usr/bin/env python
#
# Copyright ©️: 2024 Jonny Eliasson, Ulsholmen AB, Sweden
#
# License: 3-clause BSD, see the end of file.
#
"""
    Generating html-files with mutations for mutmut

    add right scrollbar indicator
    add statistics to files and index
"""
# Std
import hashlib
import html
import io
from itertools import groupby
import keyword
import pathlib
import sys
import tokenize

# local
from mutmut import (
    BAD_TIMEOUT,
    OK_SUSPICIOUS,
    BAD_SURVIVED,
    SKIPPED,
    UNTESTED,
    OK_KILLED,
    RelativeMutationID,
)
from mutmut.cache import _get_unified_diff, init_db, Mutant

# other
from pony.orm import db_session


VERSION = '2024.10.0'

# Include the highlighted code in an HTML document
html_content = """<!DOCTYPE html>
<html>
    <head>
        <link rel="stylesheet" href="htmlmut.css">
        <script type="text/javascript" src="htmlmut.js"></script>
    </head>
    <body>
        {highlighted_code}
    </body>
</html>
"""


MUTANT_STATUS2TEXT = {
    BAD_SURVIVED: 'Survivied',
    BAD_TIMEOUT: 'Timeout',
    SKIPPED: 'Skipped',
    UNTESTED: 'Untested',
    OK_SUSPICIOUS: 'Suspicious',
    OK_KILLED: 'Killed',
}

MUTANT_STATUS2BG = {
    BAD_SURVIVED: 'bgred',
    BAD_TIMEOUT: 'bgorange',
    SKIPPED: 'bgray',
    UNTESTED: 'bgred',
    OK_SUSPICIOUS: 'bggreen',
    OK_KILLED: 'bggreen',
}

COMBINE_BGS = {
    'bgred': {'bgred': 'bgred', 'bgorange': 'bgred', 'bggray': 'bgred', 'bggreen': 'bgred'},
    'bgorange': {
        'bgred': 'bgred',
        'bgorange': 'bgorange',
        'bggray': 'bgorange',
        'bggreen': 'bgorange',
    },
    'bgray': {'bgred': 'bgred', 'bgorange': 'bgorange', 'bggray': 'bggray', 'bggreen': 'bggreen'},
    'bggreen': {
        'bgred': 'bgred',
        'bgorange': 'bgorange',
        'bggray': 'bggreen',
        'bggreen': 'bggreen',
    },
}


def begin_indent(line, start, last_pos):
    if not line:
        if start[1] > 0:
            line += f'{start[1] * " "}'
    else:
        if start[1] > last_pos:
            line += f'{(start[1] - last_pos) * " "}'
    return line


def span(text, key):
    return f'<span class="{key}">{html.escape(text)}</span>'


def span_key(val):
    return span(val, 'k')


def span_str(val):
    return span(val, 's')


def span_num(val):
    return span(val, 'n')


def span_cmt(val):
    return span(val, 'c')


def handle_token(out, line, toknum, tokval, start, stop):
    if toknum == tokenize.NAME and keyword.iskeyword(tokval):
        line += span_key(tokval)
    elif toknum == tokenize.NUMBER:
        line += span_num(tokval)
    elif toknum == tokenize.STRING:
        if stop[0] > start[0]:
            # multiline string
            mline = tokval.splitlines()
            line += span_str(mline[0])
            out += [f'{line}']
            line = ''
            for deltaline in mline[1:-1]:
                line += span_str(deltaline)
                out += [f'{line}']
                line = ''
            line = span_str(mline[-1])
        else:
            line += span_str(tokval)
    elif toknum == tokenize.COMMENT:
        line += span_cmt(tokval)
    elif toknum in (tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER):
        out += [f'{line}']
        line = ''
    else:
        line += f'{html.escape(tokval)}'
    return line


def highlight_code(source):
    """Highlights source code using HTML."""
    fp = io.StringIO(source)

    tokens = tokenize.generate_tokens(fp.readline)
    out = []
    line = ''
    try:
        last_pos = 0
        for toknum, tokval, start, stop, _ in tokens:
            line = begin_indent(line, start, last_pos)
            last_pos = stop[1]
            line = handle_token(out, line, toknum, tokval, start, stop)
        if line:
            out += [f'{line}']
    except tokenize.TokenError as exc:
        out += [f'{exc}']
    return out


def get_added_lines(text):
    result = []
    for line in text.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            result.append(line[1:])
    return '\n'.join(result)


def create_hashed_html_filename(file_path, out_path, prefix='zz_'):
    fn = pathlib.Path(file_path).resolve()
    p = f'{fn.parent}'.encode('utf-8')
    name = fn.name.replace('.', '_')
    m = hashlib.md5()
    m.update(p)

    out_fn = f'{prefix}{m.hexdigest()[-12:]}_{name}.html'
    return out_path / out_fn


def copy_file_to_hashed_name(file_path, out_path, prefix='zx_'):
    buf = file_path.read_text()
    m = hashlib.md5()
    m.update(buf.encode('utf-8'))
    r_path = out_path / f'{prefix}{m.hexdigest()[-12:]}_{file_path.name}'
    r_path.write_text(buf)
    return r_path


def get_mutations_for_each_line(mutants, source, filename, dict_synonyms):
    line2mutations = {}
    for mutant in sorted(mutants, key=lambda m: m.id):
        diff = _get_unified_diff(
            source,
            filename,
            RelativeMutationID(mutant.line.line, mutant.index, mutant.line.line_number),
            dict_synonyms,
            update_cache=False,
        )
        if mutant.line.line_number not in line2mutations:
            line2mutations[mutant.line.line_number] = []
        if mutant.index == len(line2mutations[mutant.line.line_number]):
            line2mutations[mutant.line.line_number] += [[mutant, get_added_lines(diff)]]
        else:
            print(
                f'Error, lost mutation: ln = {mutant.line.line_number}:{mutant.index}'
                f'{mutant.line.line}'
            )
    return line2mutations


@init_db
@db_session
def create_html_report(dict_synonyms, directory):
    global html_content

    mutants = sorted(list((x for x in Mutant.select())), key=lambda x: x.line.sourcefile.filename)

    dir_path = pathlib.Path(directory).resolve()
    dir_path.mkdir(parents=True, exist_ok=True)

    this_dir_path = pathlib.Path(__file__).parent
    for name in ('htmlmut.js', 'htmlmut.css'):
        new_name = copy_file_to_hashed_name(this_dir_path / name, dir_path)
        html_content = html_content.replace(name, new_name.name)

    index_data = [
        '<h1>Mutation files</h1>',
        # f'Killed {len([x for x in mutants if x.status == OK_KILLED])}
        # out of {len(mutants)} mutants',
        '<table><thead><tr><th>File</th></thead>',
    ]
    # <th>Total</th><th>Skipped</th><th>Killed</th><th>% killed</th><th>Survived</th>

    for filename, mutants in groupby(mutants, key=lambda x: x.line.sourcefile.filename):
        print(filename)

        report_path = create_hashed_html_filename(filename, dir_path)

        index_data += [f'<tr><td><a href="{report_path.name}">{filename}</a></td></tr>']

        in_path = pathlib.Path(filename)

        source = in_path.read_text()

        line2mutations = get_mutations_for_each_line(mutants, source, filename, dict_synonyms)

        hl_code = highlight_code(source)

        create_html_from_source(hl_code, line2mutations, report_path)

    index_data += ['</table></body></html>']

    index_path = dir_path / 'index.html'
    index_path.write_text('\n'.join(index_data) + '\n')


def create_html_from_source(hl_code, line2mutations, report_path):
    line_no = -1
    result = ''
    mut_no = 0
    ln_w = len(f'{len(hl_code)}') + 1
    for line in hl_code:
        line_no += 1
        muts = ''
        txt_cls = 'bggreen'
        if line_no in line2mutations:
            index = 0
            muts += f'<div class="mts" id="d{line_no}" style="display: none;">\n'
            for mutant, item in line2mutations[line_no]:
                st = MUTANT_STATUS2TEXT.get(mutant.status, '--ERROR--')
                bg = MUTANT_STATUS2BG.get(mutant.status, 'bgred')
                txt_cls = COMBINE_BGS.get(txt_cls, {}).get(bg, 'bgred')
                mut_no += 1
                hl_item = highlight_code(item)
                muts += (
                    f'<p class="mt {bg}"><span class="ln">{ln_w * " "} </span>{hl_item[0]}'
                    f'<span class="r">{st} mt #{mut_no} ndx {index}</span></p>\n'
                )
                index += 1
            muts += '</div>\n'
            txt_cls = txt_cls.replace('bg', 'txt')
        if line_no in line2mutations:
            mut = (
                f'<span class="r {txt_cls}" onclick="toggle(\'d{line_no}\');">'
                f'#mts {len(line2mutations[line_no])}</span>'
            )
        else:
            mut = ''
        result += f'<p><span class="ln">{line_no:{ln_w}} </span>{line}{mut}</p>\n'
        result += muts
    output = html_content.format(highlighted_code=result)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(output)


def _main(arguments):
    create_html_report(dict_synonyms={}, directory='htmlmut')


if __name__ == '__main__':
    _main(sys.argv[1:])
#
# Copyright ©️: 2024 Jonny Eliasson, Ulsholmen AB, Sweden
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the <organization> nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
