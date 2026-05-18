"""
app/services/memory_neo4j.py

Episodic Memory Service — Neo4j Knowledge Graph Layer.

Responsibilities:
  - Store relationships between entities as graph nodes and edges
  - Query the graph for related entities given a user and context
  - Support relationship reasoning (e.g. "who does the user work with?")
  - Merge duplicate nodes to keep the graph clean over time

Graph Model:
  Nodes:
    (:User {user_id})
    (:Person {name, relationship_to_user})
    (:Project {name, status})
    (:Goal {title, deadline, status})
    (:Event {title, datetime, location})
    (:Topic {name})
    (:Emotion {label, recorded_at})
    (:Habit {name, frequency})

  Relationships (sample):
    (User)-[:KNOWS]->(Person)
    (User)-[:WORKS_ON]->(Project)
    (User)-[:HAS_GOAL]->(Goal)
    (User)-[:ATTENDED]->(Event)
    (User)-[:INTERESTED_IN]->(Topic)
    (User)-[:EXPERIENCED]->(Emotion)
    (Person)-[:COLLABORATES_ON]->(Project)
    (Goal)-[:RELATED_TO]->(Project)

All Cypher queries use parameterized inputs — no string interpolation.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession, exceptions as neo4j_exc
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.utils.logger import get_logger

log = get_logger(__name__)
settings = get_settings()


# ═══════════════════════════════════════════════════════════════════════════════
# DRIVER SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

class _Neo4jDriver:
    """
    Manages the async Neo4j driver lifecycle.
    The driver is a connection pool — one instance serves the entire app.
    """

    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None
        self._lock = asyncio.Lock()

    async def get_driver(self) -> AsyncDriver:
        if self._driver is not None:
            return self._driver

        async with self._lock:
            if self._driver is not None:
                return self._driver

            try:
                self._driver = AsyncGraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
                    max_connection_pool_size=50,
                    connection_timeout=10.0,
                )
                # Verify connectivity
                await self._driver.verify_connectivity()
                log.info(
                    "neo4j_driver_initialized",
                    uri=settings.NEO4J_URI,
                    database=settings.NEO4J_DATABASE,
                )
            except neo4j_exc.ServiceUnavailable as exc:
                log.error("neo4j_unavailable", error=str(exc))
                raise
            except Exception as exc:
                log.error("neo4j_init_failed", error=str(exc))
                raise

        return self._driver

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None
            log.info("neo4j_driver_closed")


_neo4j_driver = _Neo4jDriver()


@asynccontextmanager
async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager that yields an async Neo4j session."""
    driver = await _neo4j_driver.get_driver()
    async with driver.session(database=settings.NEO4J_DATABASE) as session:
        yield session


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

async def initialize_schema() -> None:
    """
    Creates uniqueness constraints and indexes on first startup.
    Idempotent — safe to call multiple times.
    """
    constraints = [
        "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
        "CREATE CONSTRAINT person_name_per_user IF NOT EXISTS FOR (p:Person) REQUIRE (p.user_id, p.name) IS UNIQUE",
        "CREATE CONSTRAINT project_name_per_user IF NOT EXISTS FOR (pr:Project) REQUIRE (pr.user_id, pr.name) IS UNIQUE",
        "CREATE CONSTRAINT goal_title_per_user IF NOT EXISTS FOR (g:Goal) REQUIRE (g.user_id, g.title) IS UNIQUE",
        "CREATE CONSTRAINT topic_name_per_user IF NOT EXISTS FOR (t:Topic) REQUIRE (t.user_id, t.name) IS UNIQUE",
    ]
    indexes = [
        "CREATE INDEX user_id_idx IF NOT EXISTS FOR (u:User) ON (u.user_id)",
        "CREATE INDEX event_datetime_idx IF NOT EXISTS FOR (e:Event) ON (e.datetime)",
        "CREATE INDEX goal_deadline_idx IF NOT EXISTS FOR (g:Goal) ON (g.deadline)",
    ]
    async with _get_session() as session:
        for cypher in constraints + indexes:
            try:
                await session.run(cypher)
            except Exception as exc:
                # Constraint may already exist — log and continue
                log.debug("neo4j_schema_stmt_skipped", cypher=cypher[:80], reason=str(exc))

    log.info("neo4j_schema_initialized")


# ═══════════════════════════════════════════════════════════════════════════════
# USER NODE
# ═══════════════════════════════════════════════════════════════════════════════

async def ensure_user_node(user_id: str) -> None:
    """
    Creates the root User node if it doesn't exist.
    Called once per user on first interaction.
    """
    cypher = """
    MERGE (u:User {user_id: $user_id})
    ON CREATE SET u.created_at = $now, u.last_seen = $now
    ON MATCH  SET u.last_seen = $now
    """
    async with _get_session() as session:
        await session.run(cypher, user_id=user_id, now=datetime.utcnow().isoformat())

    log.memory_op("ensure_user_node", memory_type="episodic", user_id=user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# RELATIONSHIP UPSERT
# ═══════════════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type(neo4j_exc.TransientError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
)
async def upsert_relationship(
    user_id: str,
    subject_label: str,
    subject_name: str,
    relationship_type: str,
    object_label: str,
    object_name: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """
    Creates or updates a (Subject)-[RELATIONSHIP]->(Object) triple in Neo4j.
    All nodes are MERGED (created if not exists, matched if exists).

    Args:
        user_id:           User scope. Added as property on all created nodes.
        subject_label:     Neo4j label for the subject node (e.g. "Person").
        subject_name:      Identifying name of the subject node.
        relationship_type: Relationship type in UPPER_SNAKE_CASE (e.g. "WORKS_ON").
        object_label:      Neo4j label for the object node (e.g. "Project").
        object_name:       Identifying name of the object node.
        properties:        Optional properties to set on the relationship.

    Example:
        await upsert_relationship(
            user_id="u123",
            subject_label="User", subject_name="u123",
            relationship_type="WORKS_ON",
            object_label="Project", object_name="Aayra Backend",
            properties={"since": "2024-01-01", "role": "Lead Developer"}
        )
    """
    properties = properties or {}
    properties["updated_at"] = datetime.utcnow().isoformat()
    properties["user_id"] = user_id

    # Dynamic Cypher with parameterized node labels and relationship type.
    # Labels cannot be parameterized in Cypher, so we validate them first.
    allowed_labels = {
        "User", "Person", "Project", "Goal", "Event",
        "Topic", "Emotion", "Habit", "Place", "Task",
    }
    allowed_rel_types = {
        "KNOWS", "WORKS_ON", "HAS_GOAL", "ATTENDED", "INTERESTED_IN",
        "EXPERIENCED", "COLLABORATES_ON", "RELATED_TO", "LOCATED_AT",
        "ASSIGNED_TO", "COMPLETED", "FAILED", "SCHEDULED_ON", "LIKES",
        "DISLIKES", "WORKS_WITH", "FAMILY_MEMBER", "MENTOR", "MENTEE",
    }

    if subject_label not in allowed_labels:
        raise ValueError(f"Disallowed subject_label: {subject_label!r}")
    if object_label not in allowed_labels:
        raise ValueError(f"Disallowed object_label: {object_label!r}")
    if relationship_type not in allowed_rel_types:
        raise ValueError(f"Disallowed relationship_type: {relationship_type!r}")

    cypher = f"""
    MERGE (s:{subject_label} {{name: $subject_name, user_id: $user_id}})
    ON CREATE SET s.created_at = $now
    MERGE (o:{object_label} {{name: $object_name, user_id: $user_id}})
    ON CREATE SET o.created_at = $now
    MERGE (s)-[r:{relationship_type}]->(o)
    SET r += $properties
    """

    async with _get_session() as session:
        await session.run(
            cypher,
            subject_name=subject_name,
            object_name=object_name,
            user_id=user_id,
            now=datetime.utcnow().isoformat(),
            properties=properties,
        )

    log.memory_op(
        "upsert_relationship",
        memory_type="episodic",
        user_id=user_id,
        triple=f"({subject_label}:{subject_name})-[{relationship_type}]->({object_label}:{object_name})",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE RETRIEVAL
# ═══════════════════════════════════════════════════════════════════════════════

async def get_user_knowledge_context(
    user_id: str,
    max_facts: int = 20,
) -> list[str]:
    """
    Retrieves a human-readable summary of the user's knowledge graph.
    Returns a flat list of natural-language relationship strings.

    This is called alongside Pinecone retrieval to provide structured
    relational context (who the user knows, what projects they have, etc.)

    Returns:
        List of strings like:
          "You work on a project called 'Aayra Backend'"
          "You know a person named 'Rohan' (colleague)"
          "You have a goal: 'Launch MVP by December 2025'"
    """
    cypher = """
    MATCH (u:User {user_id: $user_id})-[r]->(n)
    RETURN
        type(r)            AS rel_type,
        labels(n)[0]       AS node_label,
        n.name             AS node_name,
        properties(r)      AS rel_props,
        properties(n)      AS node_props
    ORDER BY n.created_at DESC
    LIMIT $max_facts
    """
    facts: list[str] = []

    try:
        async with _get_session() as session:
            result = await session.run(
                cypher, user_id=user_id, max_facts=max_facts
            )
            records = await result.data()

        for record in records:
            rel_type = record.get("rel_type", "").replace("_", " ").lower()
            node_label = record.get("node_label", "entity")
            node_name = record.get("node_name", "unknown")
            node_props = record.get("node_props", {})

            # Build human-readable sentence
            sentence = _triple_to_sentence(
                rel_type=rel_type,
                node_label=node_label.lower(),
                node_name=node_name,
                node_props=node_props,
            )
            if sentence:
                facts.append(sentence)

        log.memory_op(
            "retrieve_knowledge_context",
            memory_type="episodic",
            user_id=user_id,
            chunks=len(facts),
        )
        return facts

    except Exception as exc:
        log.error(
            "neo4j_context_retrieval_failed",
            user_id=user_id,
            error=str(exc),
        )
        return []


def _triple_to_sentence(
    rel_type: str,
    node_label: str,
    node_name: str,
    node_props: dict[str, Any],
) -> str:
    """
    Converts a graph triple into a natural-language sentence for LLM injection.
    """
    templates: dict[str, str] = {
        "knows": f"You know a person named '{node_name}'",
        "works on": f"You work on a {node_label} called '{node_name}'",
        "has goal": f"You have a goal: '{node_name}'",
        "attended": f"You attended an event: '{node_name}'",
        "interested in": f"You are interested in '{node_name}'",
        "experienced": f"You recently experienced the emotion: {node_name}",
        "collaborates on": f"You collaborate on '{node_name}'",
        "likes": f"You like '{node_name}'",
        "dislikes": f"You dislike '{node_name}'",
        "works with": f"You work with '{node_name}'",
        "family member": f"'{node_name}' is a family member",
        "mentor": f"'{node_name}' is your mentor",
        "assigned to": f"You are assigned to '{node_name}'",
        "completed": f"You completed '{node_name}'",
        "scheduled on": f"'{node_name}' is scheduled",
        "located at": f"'{node_name}' is located at {node_props.get('location', 'a known location')}",
    }

    sentence = templates.get(rel_type)
    if not sentence:
        return f"You have a '{rel_type}' relationship with '{node_name}'"

    # Enrich with deadline or status if present
    if node_props.get("deadline"):
        sentence += f" (deadline: {node_props['deadline']})"
    if node_props.get("status") and node_props["status"] not in ("active", ""):
        sentence += f" [status: {node_props['status']}]"

    return sentence


async def get_related_entities(
    user_id: str,
    entity_name: str,
    depth: int = 2,
) -> list[dict[str, Any]]:
    """
    Traverses the graph to find entities related to a given entity.
    Useful for answering questions like "what else connects to Project X?"

    Args:
        user_id:     User scope filter.
        entity_name: Name of the starting entity node.
        depth:       Max hops from the starting node (default 2).

    Returns:
        List of dicts: {name, label, relationship, distance}
    """
    cypher = """
    MATCH (start {name: $entity_name, user_id: $user_id})
    MATCH path = (start)-[*1..$depth]-(related {user_id: $user_id})
    WHERE related <> start
    RETURN DISTINCT
        related.name     AS name,
        labels(related)[0] AS label,
        length(path)     AS distance
    ORDER BY distance, related.name
    LIMIT 25
    """
    try:
        async with _get_session() as session:
            result = await session.run(
                cypher,
                entity_name=entity_name,
                user_id=user_id,
                depth=depth,
            )
            records = await result.data()
        return records
    except Exception as exc:
        log.error(
            "neo4j_related_entities_failed",
            user_id=user_id,
            entity_name=entity_name,
            error=str(exc),
        )
        return []


async def find_people(user_id: str) -> list[dict[str, Any]]:
    """
    Returns all Person nodes the user knows.
    Used to populate the 'Relationships' section in the Data Passport UI.
    """
    cypher = """
    MATCH (u:User {user_id: $user_id})-[r]->(p:Person)
    RETURN
        p.name        AS name,
        type(r)       AS relationship,
        p.created_at  AS first_mentioned,
        properties(p) AS details
    ORDER BY p.name
    """
    try:
        async with _get_session() as session:
            result = await session.run(cypher, user_id=user_id)
            return await result.data()
    except Exception as exc:
        log.error("neo4j_find_people_failed", user_id=user_id, error=str(exc))
        return []


async def find_active_goals(user_id: str) -> list[dict[str, Any]]:
    """
    Returns all Goal nodes that are not yet marked as completed.
    Injected into context to give Aayra awareness of what the user is
    working toward.
    """
    cypher = """
    MATCH (u:User {user_id: $user_id})-[:HAS_GOAL]->(g:Goal)
    WHERE g.status IS NULL OR g.status <> 'completed'
    RETURN
        g.title    AS title,
        g.deadline AS deadline,
        g.status   AS status,
        g.created_at AS created_at
    ORDER BY g.deadline ASC NULLS LAST
    LIMIT 10
    """
    try:
        async with _get_session() as session:
            result = await session.run(cypher, user_id=user_id)
            return await result.data()
    except Exception as exc:
        log.error("neo4j_find_goals_failed", user_id=user_id, error=str(exc))
        return []


async def record_emotion_event(
    user_id: str,
    emotion_label: str,
    confidence: float,
    trigger_context: str = "",
) -> None:
    """
    Records an emotional event as a timestamped Emotion node.
    Over time, these build a longitudinal emotional profile for burnout detection.
    """
    cypher = """
    MATCH (u:User {user_id: $user_id})
    CREATE (e:Emotion {
        label:       $label,
        confidence:  $confidence,
        context:     $context,
        recorded_at: $now,
        user_id:     $user_id
    })
    CREATE (u)-[:EXPERIENCED {at: $now}]->(e)
    """
    try:
        async with _get_session() as session:
            await session.run(
                cypher,
                user_id=user_id,
                label=emotion_label,
                confidence=confidence,
                context=trigger_context[:500],
                now=datetime.utcnow().isoformat(),
            )
        log.memory_op(
            "record_emotion",
            memory_type="episodic",
            user_id=user_id,
            emotion=emotion_label,
        )
    except Exception as exc:
        log.error(
            "neo4j_record_emotion_failed",
            user_id=user_id,
            error=str(exc),
        )


async def get_emotion_history(
    user_id: str,
    days: int = 14,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Returns the user's emotion history for the last N days.
    Used by the burnout detection model.

    Args:
        user_id: User scope.
        days:    Lookback window in days.
        limit:   Max number of records to return.
    """
    cypher = """
    MATCH (u:User {user_id: $user_id})-[:EXPERIENCED]->(e:Emotion)
    WHERE e.recorded_at >= $cutoff
    RETURN
        e.label       AS emotion,
        e.confidence  AS confidence,
        e.recorded_at AS recorded_at,
        e.context     AS context
    ORDER BY e.recorded_at DESC
    LIMIT $limit
    """
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    try:
        async with _get_session() as session:
            result = await session.run(
                cypher,
                user_id=user_id,
                cutoff=cutoff,
                limit=limit,
            )
            return await result.data()
    except Exception as exc:
        log.error(
            "neo4j_emotion_history_failed",
            user_id=user_id,
            error=str(exc),
        )
        return []


async def delete_user_graph(user_id: str) -> int:
    """
    Deletes ALL nodes and relationships for a given user.
    Called when a user exercises right-to-erasure (GDPR/DPDP compliance).

    Returns:
        Number of nodes deleted.
    """
    cypher = """
    MATCH (n {user_id: $user_id})
    DETACH DELETE n
    RETURN count(n) AS deleted_count
    """
    try:
        async with _get_session() as session:
            result = await session.run(cypher, user_id=user_id)
            record = await result.single()
            count = record["deleted_count"] if record else 0

        log.warning(
            "user_graph_deleted",
            user_id=user_id,
            nodes_deleted=count,
        )
        return count
    except Exception as exc:
        log.error(
            "neo4j_delete_user_graph_failed",
            user_id=user_id,
            error=str(exc),
        )
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

async def ping() -> bool:
    """Returns True if Neo4j is reachable. Used by the /health endpoint."""
    try:
        driver = await _neo4j_driver.get_driver()
        await driver.verify_connectivity()
        return True
    except Exception:
        return False


async def close_driver() -> None:
    """Called on app shutdown to cleanly close the Neo4j connection pool."""
    await _neo4j_driver.close()