import os
import csv

def export_tree_to_csv(root_dir, output_csv, ignore_dirs=None, ignore_exts=None):
    """
    Walks root_dir and writes one row per file/folder to a CSV.
    Skips common noise (venv, node_modules, .git, __pycache__, migrations cache etc).
    """
    ignore_dirs = ignore_dirs or {
        '.git', '__pycache__', 'venv', '.venv', 'env',
        'node_modules', '.idea', '.vscode', 'dist', 'build',
        '.pytest_cache', '.mypy_cache'
    }
    ignore_exts = ignore_exts or {'.pyc', '.pyo', '.log'}

    rows = []
    for current_root, dirs, files in os.walk(root_dir):
        # prune ignored directories in-place so os.walk skips them
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        rel_root = os.path.relpath(current_root, root_dir)
        depth = 0 if rel_root == '.' else rel_root.count(os.sep) + 1

        # record the folder itself
        rows.append({
            'type': 'folder',
            'path': '' if rel_root == '.' else rel_root.replace(os.sep, '/'),
            'name': os.path.basename(current_root) or root_dir,
            'extension': '',
            'depth': depth
        })

        for f in sorted(files):
            ext = os.path.splitext(f)[1]
            if ext in ignore_exts:
                continue
            file_rel_path = os.path.normpath(os.path.join(rel_root, f)) if rel_root != '.' else f
            rows.append({
                'type': 'file',
                'path': file_rel_path.replace(os.sep, '/'),
                'name': f,
                'extension': ext,
                'depth': depth
            })

    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['type', 'path', 'name', 'extension', 'depth'])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r['path']))

    print(f"Done. {len(rows)} entries written to {output_csv}")


if __name__ == '__main__':
    # Point this at your project root (e.g. the OmniFlow repo folder)
    export_tree_to_csv(
        root_dir='.',
        output_csv='project_structure.csv'
    )