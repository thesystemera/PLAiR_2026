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
from database.models import StripeWebhookEvent, AIUsageEvent, AIUsageDaily, UserRadioSettings


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


LEGACY_SETTING_COLUMNS = {
    "tts_muted": "ttsMuted",
    "notifications_muted": "notificationsMuted",
    "audio_quality": "audioQuality",
    "fps_enabled": "fpsEnabled",
    "video_clips_enabled": "videoClipsEnabled",
    "visual_quality": "visualQuality",
    "lit_artwork": "litArtwork",
}
DROPPED_USER_COLUMNS = (*LEGACY_SETTING_COLUMNS, "ui_settings", "dark_mode")


def migrate_device_settings(conn):
    """Settings moved from account columns to users.device_settings (docs/SETTINGS.md)"""
    print("\n[CHECK] users.device_settings...")
    present = [column for column in LEGACY_SETTING_COLUMNS if column_exists(conn, "users", column)]
    if present:
        pairs = ", ".join(f"'{LEGACY_SETTING_COLUMNS[column]}', {column}" for column in present)
        legacy = f"jsonb_strip_nulls(jsonb_build_object({pairs}))"
        if column_exists(conn, "users", "ui_settings"):
            legacy = f"({legacy} || COALESCE(ui_settings, '{{}}'::jsonb))"
        conn.execute(text(f"""
            UPDATE users SET device_settings = jsonb_build_object('_legacy', {legacy})
            WHERE device_settings = '{{}}'::jsonb
        """))
        conn.commit()
        print(f"  [COPIED] {', '.join(present)} into device_settings._legacy")
    for column in DROPPED_USER_COLUMNS:
        if column_exists(conn, "users", column):
            conn.execute(text(f"ALTER TABLE users DROP COLUMN {column}"))
            conn.commit()
            print(f"  [DROPPED] users.{column}")


def migrate_users_table(conn):
    """Migrate users table - add any missing columns"""
    print("\n[CHECK] users table...")
    
    # Map of column names to their SQL definitions
    columns = {
        "device_settings": "JSONB DEFAULT '{}'::jsonb NOT NULL",
        "stripe_subscription_id": "VARCHAR",
        "subscription_status": "VARCHAR",
        "current_period_end": "TIMESTAMP WITH TIME ZONE",
        "upload_enhance": "BOOLEAN DEFAULT false NOT NULL",
        "upload_rights_confirmed_at": "TIMESTAMP WITH TIME ZONE",
        "last_artist_profile_id": "INTEGER",
        # Add future columns here
        # "new_column": "VARCHAR DEFAULT 'something'",
    }
    
    for col_name, col_def in columns.items():
        if not column_exists(conn, "users", col_name):
            add_column(conn, "users", col_name, col_def)
        else:
            print(f"  [OK] {col_name} already exists")

    nullable = conn.execute(text("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'password_hash'
    """)).scalar()
    if nullable == "NO":
        conn.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
        conn.commit()
        print("  [CHANGED] password_hash is optional (passkey-only accounts)")


def main():
    print("PLAiR Database Migration")
    print("=" * 30)
    
    with _sync_engine.connect() as conn:
        migrate_users_table(conn)
        migrate_device_settings(conn)
        for table_name, col_name, col_def in (("regional_items", "published_at", "TIMESTAMP WITH TIME ZONE"),
                                              ("regional_items", "latitude", "DOUBLE PRECISION"),
                                              ("regional_items", "longitude", "DOUBLE PRECISION"),
                                              ("regional_items", "area", "VARCHAR"),
                                              ("regional_items", "entities", "TEXT DEFAULT '[]' NOT NULL"),
                                              ("regional_items", "geo_radius_m", "DOUBLE PRECISION"),
                                              ("regional_items", "geo_scope", "VARCHAR"),
                                              ("news_items", "latitude", "DOUBLE PRECISION"),
                                              ("news_items", "longitude", "DOUBLE PRECISION"),
                                              ("news_items", "geo_radius_m", "DOUBLE PRECISION"),
                                              ("news_items", "geo_scope", "VARCHAR"),
                                              ("news_items", "geo_label", "VARCHAR"),
                                              ("news_items", "entities", "TEXT DEFAULT '[]' NOT NULL"),
                                              ("news_items", "category", "VARCHAR"),
                                              ("news_items", "tone", "VARCHAR"),
                                              ("news_items", "worth", "DOUBLE PRECISION"),
                                              ("news_items", "analysed_at", "TIMESTAMP WITH TIME ZONE"),
                                              ("news_items", "title_key", "VARCHAR DEFAULT '' NOT NULL"),
                                              ("news_items", "link_id", "VARCHAR"),
                                              ("news_items", "summary", "TEXT"),
                                              ("news_items", "read_status", "VARCHAR"),
                                              ("place_cache", "row_id", "BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE"),
                                              ("place_cache", "details", "TEXT"),
                                              ("event_sources", "score", "DOUBLE PRECISION DEFAULT 1.0 NOT NULL"),
                                              ("event_sources", "depth", "INTEGER DEFAULT 0 NOT NULL"),
                                              ("play_events", "region_key", "VARCHAR")):
            print(f"\n[CHECK] {table_name}.{col_name}...")
            if not column_exists(conn, table_name, col_name):
                add_column(conn, table_name, col_name, col_def)
            else:
                print(f"  [OK] {col_name} already exists")
        for table in ("pulse_demand_answers", "pulse_demand_askers", "pulse_demand"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        conn.execute(text("DROP VIEW IF EXISTS local_nuggets"))
        conn.execute(text("ALTER TABLE regional_items DROP COLUMN IF EXISTS embedding"))
        conn.execute(text("DELETE FROM regional_items WHERE kind IN ('community', 'place')"))
        conn.commit()
        if column_exists(conn, "play_events", "region_key"):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_play_events_region_key ON play_events (region_key)"))
            conn.commit()

    print("\n[CHECK] stripe_webhook_events table...")
    StripeWebhookEvent.__table__.create(bind=_sync_engine, checkfirst=True)
    print("  [OK] stripe_webhook_events")

    for table in (AIUsageEvent.__table__, AIUsageDaily.__table__, UserRadioSettings.__table__):
        print(f"\n[CHECK] {table.name} table...")
        table.create(bind=_sync_engine, checkfirst=True)
        print(f"  [OK] {table.name}")
    
    print("\n[SUCCESS] Migration complete!")
    print("Restart the server to apply changes.")


if __name__ == "__main__":
    main()
