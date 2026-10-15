"""Generate the standalone demo's root documentation index."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def main():
    rows = []
    for path in sorted(ROOT.rglob('*.md')):
        relative = path.relative_to(ROOT)
        if any(part.startswith('.') or part in ('data', 'node_modules') for part in relative.parts):
            continue
        if relative.as_posix() == 'index.md':
            continue
        content = path.read_text(encoding='utf-8-sig')
        title = re.search(r'^title:\s*(.+)$', content, re.M)
        kind = re.search(r'^type:\s*(.+)$', content, re.M)
        if not kind:
            raise ValueError(f'Missing type: {relative}')
        label = title.group(1) if title else path.stem
        rows.append(f'| {kind.group(1)} | {label} | [{relative.as_posix()}]({relative.as_posix()}) |')
    content = '\n'.join([
        '---', 'type: Index', 'title: Web AI assistant — 文档索引', '---', '',
        '# 文档索引', '', '> 由 `uv run python scripts/update_index.py` 自动生成。', '',
        '| 类型 | 文档 | 文件 |', '|---|---|---|', *rows, '',
    ])
    (ROOT / 'index.md').write_text(content, encoding='utf-8')
    print('已同步更新根目录索引')


if __name__ == '__main__':
    main()
