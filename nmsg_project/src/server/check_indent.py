with open('server_gui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    stripped = line.rstrip()
    if stripped.startswith('class ') or stripped.startswith('def '):
        indent = len(line) - len(line.lstrip())
        print(f'{i:3d} [{indent:2d}] {line.rstrip()[:80]}')
