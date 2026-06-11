"""durable backend tables

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""
from alembic import op

from server.db import Base
from server import models  # noqa: F401


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
