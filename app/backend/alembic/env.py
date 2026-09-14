#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Desc   :

import asyncio
import importlib
import pkgutil
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine


def clear_backend_import_cache():
    roots = ("core.database", "models")
    prefixes = tuple(f"{root}." for root in roots)
    for module_name in list(sys.modules):
        if module_name in roots or module_name.startswith(prefixes):
            sys.modules.pop(module_name, None)
    importlib.invalidate_caches()


clear_backend_import_cache()
from core.database import Base
import models

# Automatically import all ORM models under Models
for _, module_name, _ in pkgutil.iter_modules(models.__path__):
    importlib.import_module(f"{models.__name__}.{module_name}")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Fall back to the application runtime DATABASE_URL when alembic.ini leaves
# sqlalchemy.url empty, so `alembic upgrade head` works without editing the ini.
# NOTE: the ini stores a quoted empty string (`""`), so strip quotes/whitespace
# before treating it as unset.
_ini_url = (config.get_main_option("sqlalchemy.url") or "").strip().strip("'\"").strip()
if not _ini_url:
    from core.config import settings

    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def alembic_include_object(object, name, type_, reflected, compare_to):
    # type_ can be 'table', 'index', 'column', 'constraint'
    # Auth tables are part of target_metadata and are managed by Alembic.
    # Keep only the legacy session table outside migration ownership.
    if type_ == "table" and name in ["sessions"]:
        return False
    return True


async def run_migrations_online():
    connectable = create_async_engine(config.get_main_option("sqlalchemy.url"), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda sync_conn: context.configure(
                connection=sync_conn,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
                include_object=alembic_include_object,
            )
        )
        async with connection.begin():
            await connection.run_sync(lambda sync_conn: context.run_migrations())
    await connectable.dispose()


def run_migrations():
    try:
        # If there is no event loop currently, use asyncio.run directly
        loop = asyncio.get_running_loop()
        loop.create_task(run_migrations_online())
    except RuntimeError:
        asyncio.run(run_migrations_online())


run_migrations()
