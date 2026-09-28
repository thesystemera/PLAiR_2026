import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
from psycopg2 import pool as psycopg2_pool

from services import log_service

POOL_MIN_CONN = 1
POOL_MAX_CONN = 10

_pools = {}
_pools_lock = threading.Lock()


def _get_pool(dsn: str) -> psycopg2_pool.ThreadedConnectionPool:
    pool = _pools.get(dsn)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = psycopg2_pool.ThreadedConnectionPool(POOL_MIN_CONN, POOL_MAX_CONN, dsn)
            _pools[dsn] = pool
        return pool


class PooledConnection:
    __slots__ = ("_conn", "_pool", "_released")

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._released = False

    @property
    def raw(self):
        return self._conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in PooledConnection.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()
        return False

    def close(self):
        if self._released:
            return
        self._released = True
        _release(self._conn, self._pool)


def _release(conn, pool):
    discard = bool(conn.closed)
    if not discard:
        try:
            status = conn.get_transaction_status()
            if status == psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN:
                discard = True
            elif status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                conn.rollback()
            if not discard and conn.autocommit:
                conn.autocommit = False
        except Exception:
            discard = True

    if pool is None:
        try:
            conn.close()
        except Exception:
            pass
        return

    try:
        pool.putconn(conn, close=discard)
    except Exception as e:
        log_service.error(f"[PG_POOL] Failed to return connection: {e}")
        try:
            conn.close()
        except Exception:
            pass


def get_pooled_connection(dsn: str) -> PooledConnection:
    pool = _get_pool(dsn)
    for _ in range(3):
        try:
            conn = pool.getconn()
        except psycopg2_pool.PoolError:
            return PooledConnection(psycopg2.connect(dsn), None)
        if conn.closed:
            pool.putconn(conn, close=True)
            continue
        return PooledConnection(conn, pool)
    return PooledConnection(psycopg2.connect(dsn), None)


@contextmanager
def pg_connection(dsn: str):
    conn = get_pooled_connection(dsn)
    try:
        yield conn
    except Exception:
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def close_all_pools():
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        try:
            pool.closeall()
        except Exception:
            pass
