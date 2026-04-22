from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads(payload: str | None, default: Any) -> Any:
    if not payload:
        return default
    return json.loads(payload)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return list(value)


@dataclass(frozen=True, slots=True)
class StorySessionRecord:
    id: int
    created_at: str
    updated_at: str
    status: str
    seed_scene_id: str | None
    current_turn: int
    current_scene_id: str | None
    current_summary_id: int | None
    onboarding_id: str | None


@dataclass(frozen=True, slots=True)
class StoryTurnRecord:
    id: int
    session_id: int
    turn_number: int
    player_input: str
    narrator_output: str
    state_snapshot_id: int | None
    created_at: str
    prompt_hash: str


@dataclass(frozen=True, slots=True)
class StoryEntityRecord:
    id: int
    session_id: int
    entity_type: str
    name: str
    canonical_key: str
    properties: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class StoryEdgeRecord:
    id: int
    session_id: int
    from_entity_id: int
    to_entity_id: int
    edge_type: str
    weight: float
    turn_id: int | None
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StorySnapshotRecord:
    id: int
    session_id: int
    turn_id: int
    scene_id: str | None
    summary_text: str
    state: dict[str, Any]
    graph_focus: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class StoryEventRecord:
    id: int
    session_id: int
    turn_id: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class StoryEntityDraft:
    entity_type: str
    name: str
    canonical_key: str
    properties: Mapping[str, Any] = field(default_factory=dict)
    status: str = "active"


@dataclass(frozen=True, slots=True)
class StoryEdgeDraft:
    from_entity_key: str
    to_entity_key: str
    edge_type: str
    weight: float = 1.0
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoryEventDraft:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


class StoryStateStore:
    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is None:
            db_path = os.fspath(path) if path is not None else ":memory:"
            connection = sqlite3.connect(db_path)

        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self.ensure_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        self._connection.close()

    def ensure_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                seed_scene_id TEXT,
                current_turn INTEGER NOT NULL DEFAULT 0,
                current_scene_id TEXT,
                current_summary_id INTEGER,
                onboarding_id TEXT
            );

            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_number INTEGER NOT NULL,
                player_input TEXT NOT NULL,
                narrator_output TEXT NOT NULL,
                state_snapshot_id INTEGER,
                created_at TEXT NOT NULL,
                prompt_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, canonical_key)
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                from_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                to_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                edge_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,
                properties_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS state_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                scene_id TEXT,
                summary_text TEXT NOT NULL,
                state_json TEXT NOT NULL,
                graph_focus_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_turns_session_turn
                ON turns(session_id, turn_number);
            CREATE INDEX IF NOT EXISTS idx_entities_session_type_key
                ON entities(session_id, entity_type, canonical_key);
            CREATE INDEX IF NOT EXISTS idx_edges_session_from_type
                ON edges(session_id, from_entity_id, edge_type);
            CREATE INDEX IF NOT EXISTS idx_edges_session_to_type
                ON edges(session_id, to_entity_id, edge_type);
            CREATE INDEX IF NOT EXISTS idx_state_snapshots_session_turn
                ON state_snapshots(session_id, turn_id);
            CREATE INDEX IF NOT EXISTS idx_events_session_turn
                ON events(session_id, turn_id);
            """
        )
        self._connection.commit()

    def create_session(
        self,
        *,
        seed_scene_id: str | None = None,
        onboarding_id: str | None = None,
        status: str = "active",
        current_scene_id: str | None = None,
    ) -> int:
        now = _now()
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO sessions (
                    created_at,
                    updated_at,
                    status,
                    seed_scene_id,
                    current_turn,
                    current_scene_id,
                    current_summary_id,
                    onboarding_id
                ) VALUES (?, ?, ?, ?, 0, ?, NULL, ?)
                """,
                (now, now, status, seed_scene_id, current_scene_id, onboarding_id),
            )
            session_id = int(cursor.lastrowid)
            self.upsert_entity(
                session_id,
                entity_type="session",
                name=f"Session {session_id}",
                canonical_key=f"session:{session_id}",
                properties={
                    "status": status,
                    "seed_scene_id": seed_scene_id,
                    "onboarding_id": onboarding_id,
                },
                status="active",
                commit=False,
            )
            self._connection.commit()
            return session_id
        except Exception:
            self._connection.rollback()
            raise

    def load_session(self, session_id: int) -> StorySessionRecord | None:
        row = self._connection.execute(
            """
            SELECT id, created_at, updated_at, status, seed_scene_id, current_turn,
                   current_scene_id, current_summary_id, onboarding_id
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return StorySessionRecord(
            id=int(data["id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            status=str(data["status"]),
            seed_scene_id=data["seed_scene_id"],
            current_turn=int(data["current_turn"]),
            current_scene_id=data["current_scene_id"],
            current_summary_id=data["current_summary_id"],
            onboarding_id=data["onboarding_id"],
        )

    def latest_session(self) -> StorySessionRecord | None:
        row = self._connection.execute(
            """
            SELECT id, created_at, updated_at, status, seed_scene_id, current_turn,
                   current_scene_id, current_summary_id, onboarding_id
            FROM sessions
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return StorySessionRecord(
            id=int(data["id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            status=str(data["status"]),
            seed_scene_id=data["seed_scene_id"],
            current_turn=int(data["current_turn"]),
            current_scene_id=data["current_scene_id"],
            current_summary_id=data["current_summary_id"],
            onboarding_id=data["onboarding_id"],
        )

    def list_turns(self, session_id: int, *, limit: int | None = None) -> list[StoryTurnRecord]:
        sql = """
            SELECT id, session_id, turn_number, player_input, narrator_output,
                   state_snapshot_id, created_at, prompt_hash
            FROM turns
            WHERE session_id = ?
            ORDER BY turn_number DESC, id DESC
        """
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        records = [
            StoryTurnRecord(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                turn_number=int(row["turn_number"]),
                player_input=str(row["player_input"]),
                narrator_output=str(row["narrator_output"]),
                state_snapshot_id=row["state_snapshot_id"],
                created_at=str(row["created_at"]),
                prompt_hash=str(row["prompt_hash"]),
            )
            for row in rows
        ]
        return records

    def list_entities(
        self,
        session_id: int,
        *,
        limit: int | None = None,
        canonical_keys: Sequence[str] | None = None,
    ) -> list[StoryEntityRecord]:
        sql = """
            SELECT id, session_id, entity_type, name, canonical_key,
                   properties_json, status, created_at, updated_at
            FROM entities
            WHERE session_id = ?
        """
        params: list[Any] = [session_id]
        if canonical_keys:
            placeholders = ",".join("?" for _ in canonical_keys)
            sql += f" AND canonical_key IN ({placeholders})"
            params.extend(canonical_keys)
        sql += " ORDER BY updated_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [
            StoryEntityRecord(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                entity_type=str(row["entity_type"]),
                name=str(row["name"]),
                canonical_key=str(row["canonical_key"]),
                properties=_json_loads(row["properties_json"], {}),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def load_entities_by_id(self, session_id: int, entity_ids: Sequence[int]) -> list[StoryEntityRecord]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        rows = self._connection.execute(
            f"""
            SELECT id, session_id, entity_type, name, canonical_key,
                   properties_json, status, created_at, updated_at
            FROM entities
            WHERE session_id = ? AND id IN ({placeholders})
            ORDER BY updated_at DESC, id DESC
            """,
            [session_id, *entity_ids],
        ).fetchall()
        return [
            StoryEntityRecord(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                entity_type=str(row["entity_type"]),
                name=str(row["name"]),
                canonical_key=str(row["canonical_key"]),
                properties=_json_loads(row["properties_json"], {}),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def list_edges(self, session_id: int, *, limit: int | None = None, entity_ids: Sequence[int] | None = None) -> list[StoryEdgeRecord]:
        sql = """
            SELECT id, session_id, from_entity_id, to_entity_id, edge_type,
                   weight, turn_id, properties_json
            FROM edges
            WHERE session_id = ?
        """
        params: list[Any] = [session_id]
        if entity_ids:
            placeholders = ",".join("?" for _ in entity_ids)
            sql += f" AND (from_entity_id IN ({placeholders}) OR to_entity_id IN ({placeholders}))"
            params.extend(entity_ids)
            params.extend(entity_ids)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [
            StoryEdgeRecord(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                from_entity_id=int(row["from_entity_id"]),
                to_entity_id=int(row["to_entity_id"]),
                edge_type=str(row["edge_type"]),
                weight=float(row["weight"]),
                turn_id=row["turn_id"],
                properties=_json_loads(row["properties_json"], {}),
            )
            for row in rows
        ]

    def load_snapshot(self, snapshot_id: int) -> StorySnapshotRecord | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, turn_id, scene_id, summary_text, state_json,
                   graph_focus_json, created_at
            FROM state_snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return StorySnapshotRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            turn_id=int(row["turn_id"]),
            scene_id=row["scene_id"],
            summary_text=str(row["summary_text"]),
            state=_json_loads(row["state_json"], {}),
            graph_focus=_json_loads(row["graph_focus_json"], {}),
            created_at=str(row["created_at"]),
        )

    def latest_snapshot(self, session_id: int) -> StorySnapshotRecord | None:
        row = self._connection.execute(
            """
            SELECT id, session_id, turn_id, scene_id, summary_text, state_json,
                   graph_focus_json, created_at
            FROM state_snapshots
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return StorySnapshotRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            turn_id=int(row["turn_id"]),
            scene_id=row["scene_id"],
            summary_text=str(row["summary_text"]),
            state=_json_loads(row["state_json"], {}),
            graph_focus=_json_loads(row["graph_focus_json"], {}),
            created_at=str(row["created_at"]),
        )

    def list_events(self, session_id: int, *, limit: int | None = None) -> list[StoryEventRecord]:
        sql = """
            SELECT id, session_id, turn_id, event_type, payload_json, created_at
            FROM events
            WHERE session_id = ?
            ORDER BY id DESC
        """
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [
            StoryEventRecord(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                turn_id=int(row["turn_id"]),
                event_type=str(row["event_type"]),
                payload=_json_loads(row["payload_json"], {}),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def upsert_entity(
        self,
        session_id: int,
        *,
        entity_type: str,
        name: str,
        canonical_key: str,
        properties: Mapping[str, Any] | None = None,
        status: str = "active",
        commit: bool = True,
    ) -> StoryEntityRecord:
        now = _now()
        properties_json = _json_dumps(dict(properties or {}))
        self._connection.execute(
            """
            INSERT INTO entities (
                session_id, entity_type, name, canonical_key, properties_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, canonical_key) DO UPDATE SET
                entity_type = excluded.entity_type,
                name = excluded.name,
                properties_json = excluded.properties_json,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (session_id, entity_type, name, canonical_key, properties_json, status, now, now),
        )
        row = self._connection.execute(
            """
            SELECT id, session_id, entity_type, name, canonical_key,
                   properties_json, status, created_at, updated_at
            FROM entities
            WHERE session_id = ? AND canonical_key = ?
            """,
            (session_id, canonical_key),
        ).fetchone()
        if row is None:
            raise RuntimeError("entity upsert failed")
        if commit:
            self._connection.commit()
        return StoryEntityRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            entity_type=str(row["entity_type"]),
            name=str(row["name"]),
            canonical_key=str(row["canonical_key"]),
            properties=_json_loads(row["properties_json"], {}),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def add_edge(
        self,
        session_id: int,
        *,
        from_entity_id: int,
        to_entity_id: int,
        edge_type: str,
        weight: float = 1.0,
        turn_id: int | None = None,
        properties: Mapping[str, Any] | None = None,
        commit: bool = True,
    ) -> StoryEdgeRecord:
        now = _now()
        cursor = self._connection.execute(
            """
            INSERT INTO edges (
                session_id, from_entity_id, to_entity_id, edge_type, weight,
                turn_id, properties_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                from_entity_id,
                to_entity_id,
                edge_type,
                weight,
                turn_id,
                _json_dumps(dict(properties or {})),
            ),
        )
        row = self._connection.execute(
            """
            SELECT id, session_id, from_entity_id, to_entity_id, edge_type,
                   weight, turn_id, properties_json
            FROM edges
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError("edge insert failed")
        if commit:
            self._connection.commit()
        return StoryEdgeRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            from_entity_id=int(row["from_entity_id"]),
            to_entity_id=int(row["to_entity_id"]),
            edge_type=str(row["edge_type"]),
            weight=float(row["weight"]),
            turn_id=row["turn_id"],
            properties=_json_loads(row["properties_json"], {}),
        )

    def build_prompt_state(
        self,
        session_id: int,
        *,
        player_input: str,
        observations: Sequence[Mapping[str, Any]] | Sequence[Any] = (),
        recent_turn_limit: int = 3,
        neighborhood_limit: int = 6,
    ) -> dict[str, Any]:
        session = self.load_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")

        snapshot = self.latest_snapshot(session_id)
        snapshot_state = dict(snapshot.state) if snapshot is not None else {}
        graph_focus = dict(snapshot.graph_focus) if snapshot is not None else {}
        facts = dict(snapshot_state.get("facts", {}))
        notes = list(snapshot_state.get("notes", []))
        current_scene_id = session.current_scene_id if session.current_scene_id is not None else None
        if current_scene_id is None and snapshot is not None:
            current_scene_id = snapshot.scene_id

        recent_turns = self._load_recent_turns(session_id, recent_turn_limit)
        recent_messages = self._turns_to_messages(recent_turns)

        focus_entity_ids = self._resolve_graph_focus(session_id, graph_focus, recent_turns, neighborhood_limit)
        entities = self.load_entities_by_id(session_id, focus_entity_ids) if focus_entity_ids else self.list_entities(session_id, limit=neighborhood_limit)
        entity_ids = [entity.id for entity in entities]
        edges = self.list_edges(session_id, limit=neighborhood_limit * 2, entity_ids=entity_ids or None)

        return {
            "session_id": session.id,
            "turn": session.current_turn,
            "player_input": player_input,
            "current_scene_id": current_scene_id,
            "current_summary_id": session.current_summary_id,
            "summary_text": snapshot.summary_text if snapshot is not None else "",
            "facts": facts,
            "notes": notes,
            "recent_messages": recent_messages,
            "observations": self._normalize_observations(observations),
            "graph_focus": graph_focus,
            "entities": [self._entity_to_dict(entity) for entity in entities],
            "edges": [self._edge_to_dict(edge) for edge in edges],
            "recent_turns": [self._turn_to_dict(turn) for turn in recent_turns],
        }

    def record_turn(
        self,
        session_id: int,
        *,
        turn_number: int,
        player_input: str,
        narrator_output: str,
        prompt_state: Mapping[str, Any],
        facts: Mapping[str, str] | None = None,
        notes: Sequence[str] | None = None,
        entities: Sequence[StoryEntityDraft] | None = None,
        edges: Sequence[StoryEdgeDraft] | None = None,
        consequences: Sequence[str] | None = None,
        observations: Sequence[Mapping[str, Any]] | Sequence[Any] = (),
        scene_id: str | None = None,
    ) -> StoryTurnRecord:
        now = _now()
        resolved_scene_id = scene_id
        if resolved_scene_id is None:
            resolved_scene_id = str(prompt_state.get("current_scene_id") or "") or None
        if resolved_scene_id is None:
            session = self.load_session(session_id)
            resolved_scene_id = session.current_scene_id if session is not None else None

        prompt_hash = hashlib.sha256(_json_dumps(dict(prompt_state)).encode("utf-8")).hexdigest()
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO turns (
                    session_id, turn_number, player_input, narrator_output,
                    state_snapshot_id, created_at, prompt_hash
                ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (session_id, turn_number, player_input, narrator_output, now, prompt_hash),
            )
            turn_id = int(cursor.lastrowid)

            turn_entity = self.upsert_entity(
                session_id,
                entity_type="turn",
                name=f"Turn {turn_number}",
                canonical_key=f"turn:{turn_number}",
                properties={
                    "player_input": player_input,
                    "narrator_output": narrator_output,
                    "turn_number": turn_number,
                },
                status="active",
                commit=False,
            )
            session_entity = self.upsert_entity(
                session_id,
                entity_type="session",
                name=f"Session {session_id}",
                canonical_key=f"session:{session_id}",
                properties={
                    "current_turn": turn_number,
                    "current_scene_id": resolved_scene_id,
                },
                status="active",
                commit=False,
            )
            self.add_edge(
                session_id,
                from_entity_id=session_entity.id,
                to_entity_id=turn_entity.id,
                edge_type="contains",
                turn_id=turn_id,
                commit=False,
            )

            focus_entity_ids: list[int] = [turn_entity.id]
            for key, value in dict(facts or {}).items():
                fact_entity = self.upsert_entity(
                    session_id,
                    entity_type="fact",
                    name=str(key),
                    canonical_key=f"fact:{key}",
                    properties={"value": value},
                    status="active",
                    commit=False,
                )
                focus_entity_ids.append(fact_entity.id)
                self.add_edge(
                    session_id,
                    from_entity_id=turn_entity.id,
                    to_entity_id=fact_entity.id,
                    edge_type="mentions",
                    turn_id=turn_id,
                    commit=False,
                )

            for item in entities or []:
                entity = self.upsert_entity(
                    session_id,
                    entity_type=item.entity_type,
                    name=item.name,
                    canonical_key=item.canonical_key,
                    properties=item.properties,
                    status=item.status,
                    commit=False,
                )
                focus_entity_ids.append(entity.id)
                self.add_edge(
                    session_id,
                    from_entity_id=turn_entity.id,
                    to_entity_id=entity.id,
                    edge_type="mentions",
                    turn_id=turn_id,
                    commit=False,
                )

            entity_key_map = {
                entity.canonical_key: entity.id
                for entity in self.list_entities(session_id, limit=None)
            }
            for edge in edges or []:
                from_entity_id = entity_key_map.get(edge.from_entity_key)
                if from_entity_id is None:
                    from_entity_id = self.upsert_entity(
                        session_id,
                        entity_type="fact",
                        name=edge.from_entity_key,
                        canonical_key=edge.from_entity_key,
                        properties={},
                        status="active",
                        commit=False,
                    ).id
                to_entity_id = entity_key_map.get(edge.to_entity_key)
                if to_entity_id is None:
                    to_entity_id = self.upsert_entity(
                        session_id,
                        entity_type="fact",
                        name=edge.to_entity_key,
                        canonical_key=edge.to_entity_key,
                        properties={},
                        status="active",
                        commit=False,
                    ).id
                self.add_edge(
                    session_id,
                    from_entity_id=from_entity_id,
                    to_entity_id=to_entity_id,
                    edge_type=edge.edge_type,
                    weight=edge.weight,
                    turn_id=turn_id,
                    properties=edge.properties,
                    commit=False,
                )

            for event in self._derive_events(player_input, narrator_output, facts, consequences, observations):
                self._connection.execute(
                    """
                    INSERT INTO events (session_id, turn_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        turn_id,
                        event.event_type,
                        _json_dumps(dict(event.payload)),
                        now,
                    ),
                )

            compact_state = self._compact_state(
                prompt_state=prompt_state,
                turn_number=turn_number + 1,
                player_input=player_input,
                narrator_output=narrator_output,
                facts=facts,
                notes=notes,
                scene_id=resolved_scene_id,
                focus_entity_ids=focus_entity_ids,
                observations=observations,
            )
            snapshot = self._connection.execute(
                """
                INSERT INTO state_snapshots (
                    session_id, turn_id, scene_id, summary_text, state_json,
                    graph_focus_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_id,
                    resolved_scene_id,
                    compact_state["summary_text"],
                    _json_dumps(compact_state["state"]),
                    _json_dumps(compact_state["graph_focus"]),
                    now,
                ),
            )
            snapshot_id = int(snapshot.lastrowid)
            self._connection.execute(
                "UPDATE turns SET state_snapshot_id = ? WHERE id = ?",
                (snapshot_id, turn_id),
            )
            self._connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, current_turn = ?, current_scene_id = ?, current_summary_id = ?
                WHERE id = ?
                """,
                (now, turn_number + 1, resolved_scene_id, snapshot_id, session_id),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

        return StoryTurnRecord(
            id=turn_id,
            session_id=session_id,
            turn_number=turn_number,
            player_input=player_input,
            narrator_output=narrator_output,
            state_snapshot_id=snapshot_id,
            created_at=now,
            prompt_hash=prompt_hash,
        )

    def _compact_state(
        self,
        *,
        prompt_state: Mapping[str, Any],
        turn_number: int,
        player_input: str,
        narrator_output: str,
        facts: Mapping[str, str] | None,
        notes: Sequence[str] | None,
        scene_id: str | None,
        focus_entity_ids: Sequence[int],
        observations: Sequence[Mapping[str, Any]] | Sequence[Any],
    ) -> dict[str, Any]:
        recent_messages = _as_list(prompt_state.get("recent_messages", []))
        recent_messages.extend(
            [
                {"role": "player", "content": player_input},
                {"role": "assistant", "content": narrator_output},
            ],
        )
        recent_messages = recent_messages[-6:]

        state = {
            "turn": turn_number,
            "facts": dict(facts or {}),
            "notes": list(notes or [])[-6:],
            "recent_messages": recent_messages,
            "observations": self._normalize_observations(observations),
            "summary_text": self._summarize(narrator_output),
            "graph_focus": {
                "entity_ids": list(dict.fromkeys(int(entity_id) for entity_id in focus_entity_ids)),
            },
        }
        return {
            "summary_text": state["summary_text"],
            "state": state,
            "graph_focus": state["graph_focus"] | {"scene_id": scene_id},
        }

    def _summarize(self, narrator_output: str) -> str:
        summary = narrator_output.strip()
        if len(summary) <= 240:
            return summary
        return summary[:237].rstrip() + "..."

    def _load_recent_turns(self, session_id: int, limit: int) -> list[StoryTurnRecord]:
        return list(reversed(self.list_turns(session_id, limit=limit)))

    def _turns_to_messages(self, turns: Sequence[StoryTurnRecord]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in turns:
            messages.append({"role": "player", "content": turn.player_input})
            messages.append({"role": "assistant", "content": turn.narrator_output})
        return messages[-6:]

    def _resolve_graph_focus(
        self,
        session_id: int,
        graph_focus: Mapping[str, Any],
        recent_turns: Sequence[StoryTurnRecord],
        neighborhood_limit: int,
    ) -> list[int]:
        entity_ids = [
            int(value)
            for value in graph_focus.get("entity_ids", [])
            if str(value).isdigit()
        ]
        if entity_ids:
            return entity_ids[:neighborhood_limit]

        if recent_turns:
            recent_turn = recent_turns[-1]
            turn_entity = self._connection.execute(
                """
                SELECT id FROM entities
                WHERE session_id = ? AND canonical_key = ?
                """,
                (session_id, f"turn:{recent_turn.turn_number}"),
            ).fetchone()
            if turn_entity is not None:
                related = self._connection.execute(
                    """
                    SELECT DISTINCT to_entity_id
                    FROM edges
                    WHERE session_id = ? AND from_entity_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, int(turn_entity["id"]), neighborhood_limit),
                ).fetchall()
                return [int(row["to_entity_id"]) for row in related]

        return []

    def _derive_events(
        self,
        player_input: str,
        narrator_output: str,
        facts: Mapping[str, str] | None,
        consequences: Sequence[str] | None,
        observations: Sequence[Mapping[str, Any]] | Sequence[Any],
    ) -> list[StoryEventDraft]:
        events = [
            StoryEventDraft(event_type="player_input", payload={"text": player_input}),
            StoryEventDraft(event_type="narrator_output", payload={"text": narrator_output}),
        ]
        for key, value in dict(facts or {}).items():
            events.append(StoryEventDraft(event_type="fact", payload={"key": key, "value": value}))
        for consequence in consequences or []:
            events.append(StoryEventDraft(event_type="consequence", payload={"text": consequence}))
        for observation in observations:
            events.append(
                StoryEventDraft(
                    event_type="observation",
                    payload=self._observation_payload(observation),
                ),
            )
        return events

    def _normalize_observations(
        self,
        observations: Sequence[Mapping[str, Any]] | Sequence[Any],
    ) -> list[dict[str, Any]]:
        return [self._observation_payload(item) for item in observations]

    def _observation_payload(self, item: Mapping[str, Any] | Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return {"tool": str(item.get("tool", "")), "result": str(item.get("result", ""))}
        return {"tool": str(getattr(item, "tool", "")), "result": str(getattr(item, "result", ""))}

    def _turn_to_dict(self, turn: StoryTurnRecord) -> dict[str, Any]:
        return {
            "id": turn.id,
            "turn_number": turn.turn_number,
            "player_input": turn.player_input,
            "narrator_output": turn.narrator_output,
            "state_snapshot_id": turn.state_snapshot_id,
            "created_at": turn.created_at,
            "prompt_hash": turn.prompt_hash,
        }

    def _entity_to_dict(self, entity: StoryEntityRecord) -> dict[str, Any]:
        return {
            "id": entity.id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "canonical_key": entity.canonical_key,
            "properties": dict(entity.properties),
            "status": entity.status,
        }

    def _edge_to_dict(self, edge: StoryEdgeRecord) -> dict[str, Any]:
        return {
            "id": edge.id,
            "from_entity_id": edge.from_entity_id,
            "to_entity_id": edge.to_entity_id,
            "edge_type": edge.edge_type,
            "weight": edge.weight,
            "turn_id": edge.turn_id,
            "properties": dict(edge.properties),
        }
