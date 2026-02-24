# Systematic Code Audit Process

A methodical approach to auditing Python files for errors using automated diagnostic tools.

## Overview

This process uses three complementary tools to find actual errors (not just style issues):
1. **Ruff** - Fast linter for syntax errors and common mistakes
2. **Pylint** - Deep static analysis for logic errors
3. **Pyright** - Type checker for type-related bugs

## Prerequisites

```bash
pip install ruff pylint pyright
```

## The Process

### Step 1: Quick Syntax Check (Ruff)

Fast check for obvious errors:

```bash
cd /path/to/your/project
cd server  # or wherever your code is

ruff check services/your_file.py --output-format=concise
```

**Look for:**
- `F541` - f-strings without placeholders (actual bug)
- `F821` - undefined names (actual bug)
- `E901` - syntax errors (actual bug)
- `E999` - syntax errors (actual bug)

**Fixable automatically:**
```bash
ruff check services/your_file.py --fix
```

### Step 2: Deep Analysis (Pylint)

Comprehensive check for logic errors:

```bash
pylint services/your_file.py --disable=R,C
```

**Disable explanations:**
- `R` - Refactoring suggestions (style, not errors)
- `C` - Convention warnings (style, not errors)
- Keeping: `W` (warnings), `E` (errors), `F` (fatal)

**Look for:**
- `E0203` - Access to member before definition
- `W0613` - Unused arguments
- `W0621` - Redefining names
- `W1309` - f-strings without interpolation
- `E1102` - Not callable
- `W0718` - Catching too general Exception (may be OK)
- `W0212` - Access to protected member (internal use, usually OK)

### Step 3: Type Checking (Pyright)

Finds type-related bugs that runtime might miss:

```bash
pyright services/your_file.py
```

**Look for:**
- `reportOptionalMemberAccess` - Using None as object
- `reportOptionalSubscript` - Subscripting None
- `reportArgumentType` - Wrong argument types
- `reportIncompatibleMethodOverride` - Method signature mismatch
- `reportGeneralTypeIssues` - General type errors

### Step 4: Run All Three Together

```bash
# All in one command
ruff check services/your_file.py 2>&1
pylint services/your_file.py --disable=R,C 2>&1
pyright services/your_file.py 2>&1
```

## Example: Auditing Multiple Files

### Single File Audit

```bash
FILE="services/catalog_database_service.py"
ruff check $FILE
pylint $FILE --disable=R,C
pyright $FILE
```

### Batch Audit (Multiple Files)

```bash
# Find all catalog services
for file in services/catalog_*.py; do
    echo "=== Auditing $file ==="
    ruff check "$file" 2>&1 | head -20
    pylint "$file" --disable=R,C 2>&1 | grep -E "(E|W)\d+" | head -10
    pyright "$file" 2>&1 | head -10
    echo ""
done
```

### Audit by Pattern

```bash
# All database services
ruff check services/*database*.py
pylint services/*database*.py --disable=R,C
pyright services/*database*.py
```

## Common Errors to Fix

### 1. f-strings Without Placeholders

```python
# BAD
log.info(f"Service started")

# GOOD
log.info("Service started")
```

**Tool:** Ruff F541, Pylint W1309

### 2. Access Before Definition (Singleton Pattern)

```python
# BAD
class MyService:
    def __init__(self):
        if self._initialized:  # May not exist yet!
            return

# GOOD
class MyService:
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
```

**Tool:** Pylint E0203

### 3. None Subscriptable

```python
# BAD
row = cursor.fetchone()
value = row[0]  # row could be None

# GOOD
row = cursor.fetchone()
value = row[0] if row else default_value
```

**Tool:** Pyright reportOptionalSubscript

### 4. Unused Variables

```python
# BAD
for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    # j1, j2 never used

# GOOD
for tag, i1, i2, _, _ in matcher.get_opcodes():
```

**Tool:** Pylint W0613

## What NOT to Fix

Some warnings are intentional patterns:

| Warning | Usually OK? | Notes |
|---------|-------------|-------|
| `W0718` broad-exception | ✅ Yes | Pre-existing pattern in codebase |
| `W0212` protected-access | ✅ Yes | Internal classes accessing each other |
| `E701` multiple-statements | ⚠️ Style | `if x: return` one-liners |
| Line too long | ⚠️ Style | Not actual errors |

## Interpreting Results

### Priority Order

1. **Pyright errors** - Type safety issues (will cause runtime bugs)
2. **Pylint E-class** - Actual errors
3. **Ruff F-class** - Syntax/functionality errors
4. **Pylint W-class** - Warnings (review, may be intentional)

### Exit Codes

- `ruff`: Exit 1 if errors found
- `pylint`: Exit 0-31 depending on severity
- `pyright`: Exit 1 if errors found

## Tips

1. **Fix Pyright first** - Type errors often cause other issues
2. **Run after any refactoring** - Catch introduced bugs immediately
3. **Use `--fix` on Ruff** - Auto-fixes common issues
4. **Ignore protected-access** - If it's your own internal code
5. **Save output to file** for large audits:
   ```bash
   ruff check services/ > audit_ruff.txt 2>&1
   ```

## Quick Reference Card

```bash
# One-liner for a file
ruff check $FILE; pylint $FILE --disable=R,C; pyright $FILE

# One-liner with fixes
ruff check $FILE --fix; pylint $FILE --disable=R,C; pyright $FILE

# All services at once
for f in services/*.py; do echo "=== $f ==="; ruff check "$f" 2>&1 | grep -E "^.*:\s+[FEW]" | head -5; done
```
