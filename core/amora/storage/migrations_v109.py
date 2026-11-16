"""C.2C.3: register the certified additive sensory-episode work DDL.

Runtime application is explicitly gated; v18 remains the serving default.
The SQL is the same reviewed statement set used for the v19 isolated drill.
"""

from .sensory_episode_schema import SENSORY_EPISODE_QUEUE_CANDIDATE_SQL

MIGRATION_V109 = (19, SENSORY_EPISODE_QUEUE_CANDIDATE_SQL)
