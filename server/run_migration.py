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

from sqlalchemy import text
from database.connection import _sync_engine
from database.models import StripeWebhookEvent, AIUsageEvent, AIUsageDaily, UserRadioSettings, PulseDemand, PulseDemandAsker


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
        "stripe_subscription_id": "VARCHAR",
        "subscription_status": "VARCHAR",
        "current_period_end": "TIMESTAMP WITH TIME ZONE",
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
        for table_name, col_name, col_def in (("regional_items", "embedding", "BYTEA"),
                                              ("regional_items", "published_at", "TIMESTAMP WITH TIME ZONE"),
                                              ("regional_items", "latitude", "DOUBLE PRECISION"),
                                              ("regional_items", "longitude", "DOUBLE PRECISION"),
                                              ("regional_items", "area", "VARCHAR"),
                                              ("regional_items", "entities", "TEXT DEFAULT '[]' NOT NULL"),
                                              ("play_events", "region_key", "VARCHAR")):
            print(f"\n[CHECK] {table_name}.{col_name}...")
            if not column_exists(conn, table_name, col_name):
                add_column(conn, table_name, col_name, col_def)
            else:
                print(f"  [OK] {col_name} already exists")
        if column_exists(conn, "play_events", "region_key"):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_play_events_region_key ON play_events (region_key)"))
            conn.commit()

    print("\n[CHECK] stripe_webhook_events table...")
    StripeWebhookEvent.__table__.create(bind=_sync_engine, checkfirst=True)
    print("  [OK] stripe_webhook_events")

    for table in (AIUsageEvent.__table__, AIUsageDaily.__table__, UserRadioSettings.__table__,
                  PulseDemand.__table__, PulseDemandAsker.__table__):
        print(f"\n[CHECK] {table.name} table...")
        table.create(bind=_sync_engine, checkfirst=True)
        print(f"  [OK] {table.name}")
    
    print("\n[SUCCESS] Migration complete!")
    print("Restart the server to apply changes.")


if __name__ == "__main__":
    main()
