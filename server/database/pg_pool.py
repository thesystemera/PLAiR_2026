import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
from psycopg2 import pool as psycopg2_pool

from services import log_service

POOL_MIN_CONN = 1
POOL_MAX_CONN = 16
POOL_ACQUIRE_TIMEOUT_S = 30.0

_pools = {}
_pools_lock = threading.Lock()


class BoundedPool:
    __slots__ = ("pool", "slots", "max_conn")

    def __init__(self, dsn: str, min_conn: int, max_conn: int):
        self.pool = psycopg2_pool.ThreadedConnectionPool(min_conn, max_conn, dsn)
        self.slots = threading.BoundedSemaphore(max_conn)
        self.max_conn = max_conn

    def acquire(self, timeout: float = POOL_ACQUIRE_TIMEOUT_S):
        if not self.slots.acquire(timeout=timeout):
            log_service.error(f"[PG_POOL] No free connection after {timeout:.0f}s (pool of {self.max_conn} exhausted)")
            raise psycopg2_pool.PoolError(f"connection pool exhausted: no free connection within {timeout:.0f}s")
        try:
            for _ in range(3):
                conn = self.pool.getconn()
                if not conn.closed:
                    return conn
                self.pool.putconn(conn, close=True)
            return self.pool.getconn()
        except BaseException:
            self.slots.release()
            raise

    def release(self, conn, discard: bool):
        try:
            self.pool.putconn(conn, close=discard)
        except Exception as e:
            log_service.error(f"[PG_POOL] Failed to return connection: {e}")
            try:
                conn.close()
            except Exception:
                pass
        finally:
            self.slots.release()

    def closeall(self):
        self.pool.closeall()


def _get_pool(dsn: str) -> BoundedPool:
    pool = _pools.get(dsn)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = BoundedPool(dsn, POOL_MIN_CONN, POOL_MAX_CONN)
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

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        if self._released:
            return
        self._released = True
        _release(self._conn, self._pool)


def _release(conn, pool: BoundedPool):
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
    pool.release(conn, discard)


def get_pooled_connection(dsn: str) -> PooledConnection:
    pool = _get_pool(dsn)
    return PooledConnection(pool.acquire(), pool)


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
