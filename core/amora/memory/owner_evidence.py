"""Owner-only source evidence; historical utterances are not current facts."""
from .recall_intent import topic_terms,parse_recall_intent

PAGE=64
PAGES=8


def recall_episodes(store,owner,topic,*,exclude_turn=None,remote=False,session=None,before=None):
    terms=topic_terms(topic)
    if owner!="PERSON_BK" or not terms or (remote and (not session or before is None)):
        return {},True
    picked=[]
    complete=False
    for page in range(PAGES):
        rows=store.owner_topic_source_rows(owner,remote=remote,session=session,before=before,
                                            limit=PAGE,offset=page*PAGE)
        for row in rows:
            ident=str(row["turn_id"])
            text=row["raw_text"] or ""
            if ident==str(exclude_turn) or parse_recall_intent(text) or not terms<=topic_terms(text):
                continue
            picked.append(ident)
            if len(picked)>=3:
                break
        if len(picked)>=3:
            break
        if len(rows)<PAGE:
            complete=True
            break
    matches=store.owner_turn_conversations(owner,picked)
    if remote:
        matches={k:v for k,v in matches.items()
                 if v["remote_processing_allowed"] and v["privacy_scope"]=="PERSONAL"
                 and v["session_id"]==session and v["at"]<before}
    return dict(sorted(matches.items(),key=lambda kv:(kv[1]["at"],kv[0]))),complete


def render_episodes(episodes):
    from amora.cognition.temporal import conversation_zone
    lines=["Encontrei estes relatos no histórico original (não são confirmação de que continuam atuais):"]
    for source in episodes.values():
        at=source["at"].astimezone(conversation_zone()).strftime("%d/%m/%Y %H:%M")
        original=" ".join(source["user_text"].split())
        lines.append(f"Em {at}, você disse: “{original[:400]}{'…' if len(original)>400 else ''}”")
    return "\n\n".join(lines)[:1900]
