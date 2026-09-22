from sqlalchemy import desc
from db.models import User, ConversationHistory, Session, SessionSummary, UserPreference, Skill, Provider


class MemoryService:
    def get_or_create_user(self, db, telegram_id, username=None, full_name=None):
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None:
            user = User(telegram_id=telegram_id, username=username, full_name=full_name)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            changed = False
            if username and user.username != username:
                user.username = username
                changed = True
            if full_name and user.full_name != full_name:
                user.full_name = full_name
                changed = True
            if changed:
                db.commit()
        return user

    def get_current_session(self, db, user):
        """Return the user's active session, creating (and backfilling any
        session-less history into) one if none exists yet."""
        if user.current_session_id:
            sess = db.query(Session).filter(Session.id == user.current_session_id).first()
            if sess:
                return sess
        sess = Session(telegram_id=user.telegram_id)
        db.add(sess)
        db.commit()
        db.refresh(sess)
        # adopt any pre-session (session_id IS NULL) history as session #1
        db.query(ConversationHistory).filter(
            ConversationHistory.telegram_id == user.telegram_id,
            ConversationHistory.session_id.is_(None),
        ).update({ConversationHistory.session_id: sess.id})
        user.current_session_id = sess.id
        db.commit()
        return sess

    def new_session(self, db, user):
        sess = Session(telegram_id=user.telegram_id)
        db.add(sess)
        db.commit()
        db.refresh(sess)
        user.current_session_id = sess.id
        db.commit()
        return sess

    def resume_session(self, db, user, session_id: int):
        sess = (
            db.query(Session)
            .filter(Session.id == session_id, Session.telegram_id == user.telegram_id)
            .first()
        )
        if sess:
            user.current_session_id = sess.id
            db.commit()
        return sess

    def list_sessions(self, db, user):
        return (
            db.query(Session)
            .filter(Session.telegram_id == user.telegram_id)
            .order_by(desc(Session.started_at))
            .all()
        )

    def get_history(self, db, user, limit=None, session=None):
        session = session or self.get_current_session(db, user)
        limit = limit or (user.memory_window or 20)
        rows = (
            db.query(ConversationHistory)
            .filter(
                ConversationHistory.telegram_id == user.telegram_id,
                ConversationHistory.session_id == session.id,
            )
            .order_by(desc(ConversationHistory.timestamp))
            .limit(limit)
            .all()
        )
        rows = list(reversed(rows))
        return [{"role": r.role, "content": r.content} for r in rows]

    def add_message(self, db, user, role, content, model_used=None, session=None):
        session = session or self.get_current_session(db, user)
        db.add(
            ConversationHistory(
                telegram_id=user.telegram_id,
                session_id=session.id,
                role=role,
                content=content,
                model_used=model_used,
            )
        )
        session.message_count = (session.message_count or 0) + 1
        db.commit()

    def clear_session(self, db, user, session=None):
        session = session or self.get_current_session(db, user)
        db.query(ConversationHistory).filter(
            ConversationHistory.telegram_id == user.telegram_id,
            ConversationHistory.session_id == session.id,
        ).delete()
        session.message_count = 0
        db.commit()

    def clear_history(self, db, user):
        """Full wipe: delete all sessions and their messages for the user."""
        db.query(ConversationHistory).filter(
            ConversationHistory.telegram_id == user.telegram_id
        ).delete()
        db.query(Session).filter(Session.telegram_id == user.telegram_id).delete()
        user.current_session_id = None
        db.commit()

    def set_preference(self, db, user, key, value):
        pref = (
            db.query(UserPreference)
            .filter(UserPreference.telegram_id == user.telegram_id, UserPreference.key == key)
            .first()
        )
        if pref:
            pref.value = value
        else:
            db.add(UserPreference(telegram_id=user.telegram_id, key=key, value=value))
        db.commit()

    def delete_preference(self, db, user, key) -> bool:
        pref = (
            db.query(UserPreference)
            .filter(UserPreference.telegram_id == user.telegram_id, UserPreference.key == key)
            .first()
        )
        if pref:
            db.delete(pref)
            db.commit()
            return True
        return False

    def clear_preferences(self, db, user) -> int:
        deleted = (
            db.query(UserPreference)
            .filter(UserPreference.telegram_id == user.telegram_id)
            .delete()
        )
        db.commit()
        return deleted

    # ---- user-defined skills (personas / behavior snippets) ----

    def add_skill(self, db, user, name, instructions):
        existing = self.get_skill(db, user, name)
        if existing:
            existing.instructions = instructions
        else:
            db.add(Skill(telegram_id=user.telegram_id, name=name, instructions=instructions))
        db.commit()

    def get_skill(self, db, user, name):
        return (
            db.query(Skill)
            .filter(Skill.telegram_id == user.telegram_id, Skill.name == name)
            .first()
        )

    def list_skills(self, db, user):
        return (
            db.query(Skill)
            .filter(Skill.telegram_id == user.telegram_id)
            .order_by(Skill.name)
            .all()
        )

    def delete_skill(self, db, user, name) -> bool:
        s = self.get_skill(db, user, name)
        if s:
            db.delete(s)
            db.commit()
            return True
        return False

    def get_preference(self, db, user, key, default=None):
        pref = (
            db.query(UserPreference)
            .filter(UserPreference.telegram_id == user.telegram_id, UserPreference.key == key)
            .first()
        )
        return pref.value if pref else default

    def get_all_preferences(self, db, user):
        return {
            p.key: p.value
            for p in db.query(UserPreference)
            .filter(UserPreference.telegram_id == user.telegram_id)
            .all()
        }

    def all_users(self, db):
        return db.query(User).all()

    # ---- autocompact: summarize old session tail into SessionSummary ----

    def get_compact_summary(self, db, session_id: int) -> str:
        row = db.query(SessionSummary).filter(
            SessionSummary.session_id == session_id).first()
        return row.summary if row else ""

    def maybe_compact(self, db, user, session=None) -> bool:
        """If a session grew past 2x memory_window, summarize the oldest
        messages into SessionSummary and delete them. Returns True if compacted.
        Never raises — failures just skip compaction for this turn."""
        try:
            if (self.get_preference(db, user, "autocompact", "on") or "on").lower() in ("off", "0", "false", "no"):
                return False
            session = session or self.get_current_session(db, user)
            window = user.memory_window or 20
            threshold = max(window * 2, 40)
            count = db.query(ConversationHistory).filter(
                ConversationHistory.session_id == session.id).count()
            if count <= threshold:
                return False
            keep = window
            old_rows = (
                db.query(ConversationHistory)
                .filter(ConversationHistory.session_id == session.id)
                .order_by(ConversationHistory.id.asc())
                .limit(count - keep)
                .all()
            )
            if not old_rows:
                return False
            from services.llm_client import chat_completion as _chat
            transcript = "\n".join(f"{r.role}: {r.content[:800]}" for r in old_rows)
            prev = self.get_compact_summary(db, session.id)
            prompt = ("Summarize this conversation compactly for future context. "
                      "Keep facts, decisions, names, preferences. Under 500 words.\n\n"
                      + (f"Previous summary:\n{prev}\n\n" if prev else "")
                      + f"New messages:\n{transcript}")
            summary = _chat(user, [
                {"role": "system", "content": "You compress chat history into dense memory."},
                {"role": "user", "content": prompt},
            ], max_tokens=800, temperature=0.3)
            row = db.query(SessionSummary).filter(
                SessionSummary.session_id == session.id).first()
            if row:
                row.summary = summary
            else:
                db.add(SessionSummary(session_id=session.id,
                                      telegram_id=user.telegram_id, summary=summary))
            for r in old_rows:
                db.delete(r)
            db.commit()
            return True
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            return False

    # ---- providers (multi base_url + key) ----

    def add_provider(self, db, user, name, base_url, api_key_encrypted=None):
        from services.llm_client import encrypt_key as _enc
        enc = api_key_encrypted
        # allow passing raw key: encrypt if it doesn't look encrypted
        if enc and not enc.startswith("gAAAA"):
            try:
                enc = _enc(enc)
            except Exception:
                pass
        existing = db.query(Provider).filter(
            Provider.telegram_id == user.telegram_id, Provider.name == name).first()
        if existing:
            existing.base_url = base_url
            if enc:
                existing.api_key_encrypted = enc
            existing.enabled = True
        else:
            db.add(Provider(telegram_id=user.telegram_id, name=name,
                            base_url=base_url, api_key_encrypted=enc))
        db.commit()

    def list_providers(self, db, user):
        return db.query(Provider).filter(
            Provider.telegram_id == user.telegram_id).order_by(Provider.name).all()

    def delete_provider(self, db, user, name) -> bool:
        r = db.query(Provider).filter(
            Provider.telegram_id == user.telegram_id, Provider.name == name).first()
        if r:
            db.delete(r)
            db.commit()
            return True
        return False
