"""custom_agent_sharing

Revision ID: 20250303164854_414f9ab20475
Revises: 82eb6e97aed3
Create Date: 2025-03-03 16:48:54.711260

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20250303164854_414f9ab20475'
down_revision: Union[str, None] = '82eb6e97aed3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('custom_agents', sa.Column('visibility', sa.String(), nullable=False))
    op.alter_column('custom_agents', 'user_id',
               existing_type=sa.VARCHAR(),
               nullable=False)
    op.alter_column('custom_agents', 'deployment_status',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.alter_column('custom_agents', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.alter_column('custom_agents', 'updated_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.drop_index('ix_custom_agents_id', table_name='custom_agents')
    op.drop_index('ix_custom_agents_user_id', table_name='custom_agents')
    op.create_foreign_key(None, 'custom_agents', 'users', ['user_id'], ['uid'])

    # ### end Alembic commands ###


def downgrade() -> None:

    op.drop_constraint(None, 'custom_agents', type_='foreignkey')
    op.create_index('ix_custom_agents_user_id', 'custom_agents', ['user_id'], unique=False)
    op.create_index('ix_custom_agents_id', 'custom_agents', ['id'], unique=False)
    op.alter_column('custom_agents', 'updated_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('custom_agents', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('custom_agents', 'deployment_status',
               existing_type=sa.VARCHAR(),
               nullable=False)
    op.alter_column('custom_agents', 'user_id',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.drop_column('custom_agents', 'visibility')
    # ### end Alembic commands ###
