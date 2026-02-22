"""add elo and streaks to user

Revision ID: e8f1a2b3c4d5
Revises: d7903cc99437
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'e8f1a2b3c4d5'
down_revision = 'd7903cc99437'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('elo',         sa.Integer(), nullable=True, server_default='1200'))
        batch_op.add_column(sa.Column('win_streak',  sa.Integer(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('best_streak', sa.Integer(), nullable=True, server_default='0'))
    # Fill in defaults for existing rows
    op.execute("UPDATE \"user\" SET elo=1200 WHERE elo IS NULL")
    op.execute("UPDATE \"user\" SET win_streak=0  WHERE win_streak IS NULL")
    op.execute("UPDATE \"user\" SET best_streak=0 WHERE best_streak IS NULL")

def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('best_streak')
        batch_op.drop_column('win_streak')
        batch_op.drop_column('elo')
