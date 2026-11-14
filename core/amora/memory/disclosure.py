"""Storage predicate for one-turn owner-authorized disclosure.

This is not a new memory scope or a reusable permission. Restrict disclosure to
active PERSONAL records owned by the verified requester, without third-party
audiences or derived sources. Inferences need a persisted owner DM as evidence.
"""
from __future__ import annotations

# SQL aliases are fixed by callers, never user input.
SHAREABLE_MEMORY_SQL = """
m.owner_entity_id=? AND m.privacy_scope='PERSONAL'
AND m.archived_at IS NULL
AND (m.valid_from IS NULL OR m.valid_from<=?)
AND (m.valid_until IS NULL OR m.valid_until>=?)
AND json_array_length(m.derived_from_json)=0
AND NOT EXISTS (SELECT 1 FROM json_each(m.audience_json) a WHERE a.value<>m.owner_entity_id)
AND (
    (m.provenance_kind='user_assertion' AND m.actor_id=m.owner_entity_id)
    OR (
        m.provenance_kind='inference' AND m.actor_id='ENTITY_AMORA'
        AND m.provenance_source LIKE 'cognition:%'
        AND EXISTS (
            SELECT 1 FROM conversation_messages s
            WHERE s.turn_id=json_extract(m.metadata_json,'$.turn_id')
              AND s.dedupe_key='cognitive-input:' || s.turn_id
              AND s.speaker_id=m.owner_entity_id AND s.session_id=m.session_id
              AND s.privacy_scope='PERSONAL'
              AND json_extract(s.metadata_json,'$.surface')='platform_messaging'
              AND json_extract(s.metadata_json,'$.channel_type')='dm'
              AND COALESCE(json_extract(s.metadata_json,'$.test_mode'),0)=0
              AND NOT EXISTS (
                  SELECT 1 FROM memory_reflection_invalidations x
                  WHERE x.session_id=s.session_id
                    AND x.native_message_hash=json_extract(s.metadata_json,'$.native_message_id_hash')
                    AND x.invalidated_through>=COALESCE(json_extract(s.metadata_json,'$.transport_revision'),1)
              )
        )
    )
)
"""
