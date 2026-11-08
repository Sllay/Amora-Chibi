"""Directional interpretation of graph edges, shared by social consumers."""
PARENT_EDGES = frozenset({'father_of', 'mother_of', 'parent_of', 'father', 'mother', 'parent', 'pai', 'mae'})
CHILD_EDGES = frozenset({'daughter_of', 'son_of', 'child_of', 'family_child_parent'})


def is_parent_of(relations, parent, child):
    return any((r.get('source') == parent and r.get('target') == child and r.get('relation_type') in PARENT_EDGES)
               or (r.get('target') == parent and r.get('source') == child and r.get('relation_type') in CHILD_EDGES)
               for r in relations)
