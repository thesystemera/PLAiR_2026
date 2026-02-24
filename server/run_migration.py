# -*- coding: utf-8 -*-
"""
Database Migration Runner for PLAiR.fm

Run this after adding new columns to models.py:
    E:/AI_RADIO/.venv/Scripts/python.exe server/run_migration.py

This automatically detects and adds missing columns to PostgreSQL tables.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text, inspect
from database.connection import _sync_engine
from database.models import Base, User


def column_exists(conn, table_name, column_name):
    """Check if a column exists in a table"""
    result = conn.execute(text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = :table AND column_name = :column
    """), {"table": table_name, "column": column_name})
    return result.fetchone() is not None


def add_column(conn, table_name, column_name, column_def):
    """Add a column to a table"""
    conn.execute(text(f"""
        ALTER TABLE {table_name} 
        ADD COLUMN {column_name} {column_def}
    """))
    conn.commit()
    print(f"  [ADDED] {column_name} to {table_name}")


def migrate_users_table(conn):
    """Migrate users table - add any missing columns"""
    print("\n[CHECK] users table...")
    
    # Map of column names to their SQL definitions
    columns = {
        "visual_quality": "VARCHAR DEFAULT 'high' NOT NULL",
        # Add future columns here
        # "new_column": "VARCHAR DEFAULT 'something'",
    }
    
    for col_name, col_def in columns.items():
        if not column_exists(conn, "users", col_name):
            add_column(conn, "users", col_name, col_def)
        else:
            print(f"  [OK] {col_name} already exists")


def main():
    print("PLAiR Database Migration")
    print("=" * 30)
    
    with _sync_engine.connect() as conn:
        migrate_users_table(conn)
    
    print("\n[SUCCESS] Migration complete!")
    print("Restart the server to apply changes.")


if __name__ == "__main__":
    main()
