#!/usr/bin/env python3
"""
Dead Function Finder - Finds Python functions that are never called.

Understands:
- FastAPI route decorators (@app.get, @app.post, etc.)
- Context node registrations (@registry.register)
- Dunder methods (__init__, __aenter__, etc.)
- Service class methods called from app.py routes

Usage:
    python find_dead_functions.py
"""

import ast
from pathlib import Path
from collections import defaultdict

# Directories to scan for function definitions
SCAN_DIRS = [
    'services',
    'services_radio',
]

# Directories to search for function calls (entire codebase)
SEARCH_DIRS = [
    '.',  # server root
]

# Patterns to ignore (known false positives)
IGNORE_PATTERNS = [
    r'^__.*__$',           # Dunder methods
    r'^_',                 # Private methods (often called internally)
    r'^test_',             # Test functions
    r'^get_.*_instruction', # Context node instruction getters (registered dynamically)
    r'^get_.*_data',       # Context node data getters (registered dynamically)
]

# Decorators that indicate the function IS used (via framework)
USED_DECORATORS = [
    'app.get', 'app.post', 'app.put', 'app.delete', 'app.patch', 'app.websocket',
    'router.get', 'router.post', 'router.put', 'router.delete',
    'registry.register',
    'staticmethod', 'classmethod', 'property',
    'asynccontextmanager', 'contextmanager',
]


class FunctionFinder(ast.NodeVisitor):
    """Finds all function definitions in a file."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.functions = []  # [(name, lineno, is_method, decorators)]
        self.current_class = None

    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node):
        decorators = [self._get_decorator_name(d) for d in node.decorator_list]
        self.functions.append({
            'name': node.name,
            'lineno': node.lineno,
            'is_method': self.current_class is not None,
            'class_name': self.current_class,
            'decorators': decorators,
            'filepath': self.filepath,
        })
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _get_decorator_name(self, decorator):
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            parts = []
            node = decorator
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return '.'.join(reversed(parts))
        elif isinstance(decorator, ast.Call):
            return self._get_decorator_name(decorator.func)
        return ''


def find_all_functions(base_path, scan_dirs):
    """Find all function definitions in the given directories."""
    functions = []

    for scan_dir in scan_dirs:
        dir_path = base_path / scan_dir
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob('*.py'):
            if '__pycache__' in str(py_file):
                continue
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    source = f.read()
                tree = ast.parse(source)
                finder = FunctionFinder(str(py_file.relative_to(base_path)))
                finder.visit(tree)
                functions.extend(finder.functions)
            except (SyntaxError, UnicodeDecodeError) as e:
                print(f"  Warning: Could not parse {py_file}: {e}")

    return functions


def should_ignore(func):
    """Check if function should be ignored based on patterns."""
    name = func['name']

    # Check ignore patterns
    for pattern in IGNORE_PATTERNS:
        if re.match(pattern, name):
            return True

    # Check if has framework decorator
    for dec in func['decorators']:
        for used_dec in USED_DECORATORS:
            if used_dec in dec:
                return True

    return False


def find_function_calls(base_path, search_dirs, function_names):
    """Search for calls to the given function names."""
    calls = defaultdict(set)  # function_name -> set of files that call it

    # Build regex pattern for all function names
    # Match: func_name( or .func_name( or func_name as identifier

    for search_dir in search_dirs:
        dir_path = base_path / search_dir
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob('*.py'):
            if '__pycache__' in str(py_file):
                continue
            if 'find_dead_functions.py' in str(py_file):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                rel_path = str(py_file.relative_to(base_path))

                # Remove definition lines to avoid false matches
                lines = [line for line in content.split('\n')
                         if not re.match(r'\s*(async\s+)?def\s+', l)]
                filtered_content = '\n'.join(lines)

                for name in function_names:
                    # Look for function calls AND references (callbacks don't have parens)
                    patterns = [
                        rf'\b{re.escape(name)}\s*\(',      # direct call: func(
                        rf'\.{re.escape(name)}\s*\(',      # method call: obj.func(
                        rf'\.{re.escape(name)}\s*[,\)\]\n]', # callback ref: obj.func, or obj.func)
                        rf'={re.escape(name)}\s*[,\)\]\n]',  # assignment as callback: =func,
                        rf'self\.{re.escape(name)}\b',     # self reference
                    ]

                    for pattern in patterns:
                        if re.search(pattern, filtered_content):
                            calls[name].add(rel_path)
                            break

            except (UnicodeDecodeError, PermissionError):
                continue

    return calls


def main():
    base_path = Path(__file__).parent.parent  # server/

    print("=" * 70)
    print("DEAD FUNCTION FINDER")
    print("=" * 70)
    print(f"\nScanning: {', '.join(SCAN_DIRS)}")
    print("Searching: entire server/ directory")
    print()

    # Find all functions
    print("Finding function definitions...")
    all_functions = find_all_functions(base_path, SCAN_DIRS)
    print(f"  Found {len(all_functions)} total functions/methods")

    # Filter out ignored functions
    candidates = [f for f in all_functions if not should_ignore(f)]
    print(f"  After filtering: {len(candidates)} candidates to check")
    print()

    # Get unique function names
    function_names = set(f['name'] for f in candidates)

    # Search for calls
    print("Searching for function calls...")
    calls = find_function_calls(base_path, ['.'], function_names)
    print()

    # Find dead functions (defined but never called)
    dead_functions = []
    for func in candidates:
        name = func['name']
        callers = calls.get(name, set())

        # Function is used if called from ANY file (including its own)
        if not callers:
            dead_functions.append(func)

    # Sort by file and line number
    dead_functions.sort(key=lambda f: (f['filepath'], f['lineno']))

    # Report
    print("=" * 70)
    print(f"POTENTIALLY DEAD FUNCTIONS: {len(dead_functions)}")
    print("=" * 70)
    print()

    current_file = None
    for func in dead_functions:
        if func['filepath'] != current_file:
            current_file = func['filepath']
            print(f"\n{current_file}:")

        if func['is_method']:
            print(f"  Line {func['lineno']:4d}: {func['class_name']}.{func['name']}()")
        else:
            print(f"  Line {func['lineno']:4d}: {func['name']}()")

    print()
    print("=" * 70)
    print("NOTE: Review these manually. Some may be:")
    print("  - Called dynamically (getattr, etc.)")
    print("  - Used by external code")
    print("  - Part of an interface/protocol")
    print("=" * 70)


if __name__ == '__main__':
    main()
