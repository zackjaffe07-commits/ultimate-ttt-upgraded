"""Add tiebreaker and fix match model

Revision ID: d7903cc99437
Revises: adb266c13fe5
Create Date: 2026-02-17 23:49:44.076897

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7903cc99437'
down_revision = 'adb266c13fe5'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the old table
    op.drop_table('match')
    # Create the new table with the correct structure
    op.create_table('match',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player1_id', sa.Integer(), nullable=False),
        sa.Column('player2_id', sa.Integer(), nullable=False),
        sa.Column('winner_id', sa.Integer(), nullable=True),
        sa.Column('is_draw', sa.Boolean(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['player1_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['player2_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['winner_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    # Drop the new table
    op.drop_table('match')
    # Recreate the old table
    op.create_table('match',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('winner_id', sa.Integer(), nullable=True),
        sa.Column('loser_id', sa.Integer(), nullable=True),
        sa.Column('is_draw', sa.Boolean(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['loser_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['winner_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
