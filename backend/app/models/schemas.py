"""
app/models/schemas.py

All Pydantic v2 data models for Aayra's API layer.

Covers:
  - Inbound REST payloads (chat, memory operations)
  - Outbound REST responses (AI replies, memory results)
  - WebSocket message contracts (voice streaming)
  - Internal agent state models (LangGraph typed state)
  - Shared enums and base types
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class EmotionLabel(str, Enum):
    """Detected emotional states from Hume AI or text sentiment analysis."""
    NEUTRAL = "neutral"
    JOY = "joy"
    SADNESS = "sadness"
    ANGER = "anger"
    FEAR = "fear"
    SURPRISE = "surprise"
    DISGUST = "disgust"
    ANXIETY = "anxiety"
    EXCITEMENT = "excitement"
    FATIGUE = "fatigue"
    FRUSTRATION = "frustration"


class AgentStatus(str, Enum):
    """Tracks which phase of the LangGraph loop is active."""
    PLANNING = "planning"
    EXECUTING = "executing"
    CRITIQUING = "critiquing"
    RESPONDING = "responding"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryType(str, Enum):
    EPISODIC = "episodic"       # Stored in Neo4j (events, relationships)
    SEMANTIC = "semantic"       # Stored in Pinecone (facts, preferences)
    PROCEDURAL = "procedural"   # How user prefers tasks done


class WebSocketEventType(str, Enum):
    """All possible WebSocket message types for the voice/text streaming protocol."""
    # Client → Server
    AUDIO_CHUNK = "audio_chunk"
    TEXT_MESSAGE = "text_message"
    INTERRUPT = "interrupt"
    HEARTBEAT_PING = "heartbeat_ping"
    # Server → Client
    AUDIO_RESPONSE = "audio_response"
    TEXT_RESPONSE = "text_response"
    AGENT_STATUS = "agent_status"
    EMOTION_UPDATE = "emotion_update"
    TOOL_CALL_NOTIFICATION = "tool_call_notification"
    APPROVAL_REQUIRED = "approval_required"
    ERROR = "error"
    HEARTBEAT_PONG = "heartbeat_pong"
    SESSION_ENDED = "session_ended"


# ═══════════════════════════════════════════════════════════════════════════════
# BASE / SHARED MODELS
# ═══════════════════════════════════════════════════════════════════════════════


class TimestampMixin(BaseModel):
    """Adds auto-populated created_at to any model."""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(BaseModel):
    """A single turn in a conversation. Used in history arrays."""
    role: MessageRole
    content: str = Field(..., min_length=1, max_length=32_000)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    emotion: EmotionLabel | None = None

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        return v.strip()


class EmotionAnalysis(BaseModel):
    """Emotion state detected for a given input turn."""
    primary_emotion: EmotionLabel = EmotionLabel.NEUTRAL
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    valence: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Positive = pleasant, Negative = unpleasant. Range: -1.0 to 1.0",
    )
    arousal: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="High = excited/alert, Low = calm/tired. Range: -1.0 to 1.0",
    )
    raw_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Full emotion → score mapping from Hume AI.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REST API — INBOUND REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    """
    POST /api/v1/chat
    Standard text-based chat request from the frontend.
    """
    user_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique user identifier from Supabase auth.",
    )
    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Groups messages into a conversation session. "
                    "Frontend should persist this for the duration of a chat.",
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=8_000,
        description="The user's raw text input.",
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=40,
        description="Recent conversation turns for in-context memory. "
                    "Max 40 entries (20 turns). Older context is served via RAG.",
    )
    emotion_context: EmotionAnalysis | None = Field(
        default=None,
        description="Optional emotion state from Hume AI (populated by voice route).",
    )
    enable_tools: bool = Field(
        default=True,
        description="Set to False to disable agentic tool calls (pure conversational mode).",
    )

    @field_validator("message")
    @classmethod
    def strip_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message cannot be empty or whitespace only.")
        return stripped


class MemoryUpsertRequest(BaseModel):
    """
    POST /api/v1/memory/upsert
    Manually inject a fact or event into the memory system.
    Used by the consolidation engine and admin tooling.
    """
    user_id: str = Field(..., min_length=1, max_length=128)
    memory_type: MemoryType
    content: str = Field(..., min_length=5, max_length=4_000)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional key-value metadata: source, tags, importance_score, etc.",
    )


class MemorySearchRequest(BaseModel):
    """
    POST /api/v1/memory/search
    Semantic search over the user's Pinecone memory store.
    """
    user_id: str = Field(..., min_length=1, max_length=128)
    query: str = Field(..., min_length=3, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)
    memory_types: list[MemoryType] = Field(
        default_factory=lambda: [MemoryType.SEMANTIC, MemoryType.EPISODIC],
    )


class AgentApprovalRequest(BaseModel):
    """
    POST /api/v1/agent/approve
    User approves or rejects a sandboxed high-risk agent action.
    """
    session_id: str
    action_id: str = Field(
        ...,
        description="UUID of the pending action from AgentApprovalRequired.",
    )
    approved: bool
    user_note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional user feedback when rejecting.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REST API — OUTBOUND RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════


class ToolCallRecord(BaseModel):
    """Audit record for a single tool invocation by an agent."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str | None = None
    success: bool = True
    error_message: str | None = None
    duration_ms: float | None = None


class ChatResponse(TimestampMixin):
    """
    Response body for POST /api/v1/chat.
    """
    session_id: str
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str = Field(..., description="Aayra's final text response to the user.")
    agent_status: AgentStatus = AgentStatus.COMPLETED
    emotion_detected: EmotionAnalysis | None = None
    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list,
        description="Ordered list of tools invoked during this turn.",
    )
    memory_context_used: list[str] = Field(
        default_factory=list,
        description="Snippet summaries of memory chunks retrieved for this turn.",
    )
    requires_approval: bool = False
    approval_action_id: str | None = Field(
        default=None,
        description="Set when requires_approval=True. Pass to /agent/approve.",
    )
    tokens_used: int | None = None
    latency_ms: float | None = None


class MemorySearchResult(BaseModel):
    """A single memory chunk returned from semantic search."""
    memory_id: str
    content: str
    memory_type: MemoryType
    score: float = Field(ge=0.0, le=1.0, description="Cosine similarity score.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class MemorySearchResponse(BaseModel):
    """Response body for POST /api/v1/memory/search."""
    user_id: str
    query: str
    results: list[MemorySearchResult]
    total_found: int


class HealthCheckResponse(BaseModel):
    """GET /health — liveness + dependency status."""
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    environment: str
    dependencies: dict[str, Literal["ok", "error"]] = Field(
        default_factory=dict,
        description="Status of each external dependency (neo4j, pinecone, gemini, etc.)",
    )
    uptime_seconds: float | None = None


class ErrorResponse(BaseModel):
    """Standard error envelope for all 4xx/5xx responses."""
    error: str = Field(..., description="Machine-readable error code, e.g. 'memory_not_found'")
    detail: str = Field(..., description="Human-readable description for the developer.")
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET MESSAGE CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════


class WebSocketBaseMessage(BaseModel):
    """
    Every WebSocket frame — in both directions — must conform to this envelope.
    The 'type' field is the discriminator for routing on both ends.
    """
    type: WebSocketEventType
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── Client → Server ────────────────────────────────────────────────────────────

class WSAudioChunkMessage(WebSocketBaseMessage):
    """
    Client streams raw audio PCM data as base64-encoded chunks.
    Backend decodes, buffers, and passes to Whisper/Deepgram for STT.
    """
    type: Literal[WebSocketEventType.AUDIO_CHUNK] = WebSocketEventType.AUDIO_CHUNK
    user_id: str
    audio_data: str = Field(
        ...,
        description="Base64-encoded raw PCM audio chunk (16kHz, mono, int16).",
    )
    chunk_index: int = Field(ge=0, description="Sequential index for ordering chunks.")
    is_final: bool = Field(
        default=False,
        description="True on the last chunk of a user utterance (VAD detected silence).",
    )


class WSTextMessage(WebSocketBaseMessage):
    """Client sends a text message over WebSocket (non-voice chat mode)."""
    type: Literal[WebSocketEventType.TEXT_MESSAGE] = WebSocketEventType.TEXT_MESSAGE
    user_id: str
    content: str = Field(..., min_length=1, max_length=8_000)
    emotion_context: EmotionAnalysis | None = None

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        return v.strip()


class WSInterruptMessage(WebSocketBaseMessage):
    """Client sends to interrupt the current AI response (barge-in)."""
    type: Literal[WebSocketEventType.INTERRUPT] = WebSocketEventType.INTERRUPT
    user_id: str


# ── Server → Client ────────────────────────────────────────────────────────────

class WSAudioResponseMessage(WebSocketBaseMessage):
    """
    Server streams TTS audio back to the client in chunks.
    Client buffers and plays sequentially.
    """
    type: Literal[WebSocketEventType.AUDIO_RESPONSE] = WebSocketEventType.AUDIO_RESPONSE
    audio_data: str = Field(..., description="Base64-encoded MP3/Opus audio chunk.")
    chunk_index: int = Field(ge=0)
    is_final: bool = False
    sample_rate: int = Field(default=22_050)
    encoding: Literal["mp3", "opus", "pcm"] = "mp3"


class WSTextResponseMessage(WebSocketBaseMessage):
    """Server streams text tokens to the client (for caption display during voice)."""
    type: Literal[WebSocketEventType.TEXT_RESPONSE] = WebSocketEventType.TEXT_RESPONSE
    content: str
    is_final: bool = False


class WSAgentStatusMessage(WebSocketBaseMessage):
    """Server notifies client of the current LangGraph agent phase."""
    type: Literal[WebSocketEventType.AGENT_STATUS] = WebSocketEventType.AGENT_STATUS
    status: AgentStatus
    detail: str | None = Field(
        default=None,
        description="Human-readable detail, e.g. 'Checking your Google Calendar...'",
    )


class WSEmotionUpdateMessage(WebSocketBaseMessage):
    """Server sends detected emotion update after processing user audio/text."""
    type: Literal[WebSocketEventType.EMOTION_UPDATE] = WebSocketEventType.EMOTION_UPDATE
    emotion: EmotionAnalysis


class WSToolCallNotification(WebSocketBaseMessage):
    """Server notifies client that an agent tool is being invoked."""
    type: Literal[WebSocketEventType.TOOL_CALL_NOTIFICATION] = WebSocketEventType.TOOL_CALL_NOTIFICATION
    tool_name: str
    tool_description: str = Field(
        ...,
        description="Human-readable description shown in the UI activity feed.",
    )


class WSApprovalRequired(WebSocketBaseMessage):
    """
    Server requests explicit user approval before executing a high-risk tool.
    Client must display a confirmation dialog and respond via POST /agent/approve.
    """
    type: Literal[WebSocketEventType.APPROVAL_REQUIRED] = WebSocketEventType.APPROVAL_REQUIRED
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_summary: str = Field(
        ...,
        description="Plain-language description of the action requiring approval.",
    )
    action_details: dict[str, Any] = Field(
        default_factory=dict,
        description="Full structured payload of the proposed action for display.",
    )
    risk_level: Literal["medium", "high", "critical"] = "high"
    expires_at: datetime = Field(
        description="Approval window. Action is cancelled if not approved by this time.",
    )


class WSErrorMessage(WebSocketBaseMessage):
    """Server sends a non-fatal error notification to the client."""
    type: Literal[WebSocketEventType.ERROR] = WebSocketEventType.ERROR
    error_code: str
    detail: str
    recoverable: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL — LANGGRAPH AGENT STATE
# ═══════════════════════════════════════════════════════════════════════════════


class AgentState(BaseModel):
    """
    The typed state object threaded through the entire LangGraph execution graph.
    Every node reads from and writes to this state. Immutable fields are
    set at graph entry; mutable fields are updated by nodes.

    This model is NOT serialized to the API layer directly — it is internal
    to the agent_langgraph.py service.
    """

    # ── Immutable (set at graph entry) ────────────────────────────────────────
    user_id: str
    session_id: str
    user_message: str
    chat_history: list[ChatMessage] = Field(default_factory=list)
    emotion_context: EmotionAnalysis = Field(default_factory=EmotionAnalysis)
    enable_tools: bool = True

    # ── Mutable (updated by nodes) ────────────────────────────────────────────
    status: AgentStatus = AgentStatus.PLANNING
    plan: list[str] = Field(
        default_factory=list,
        description="Ordered list of sub-tasks generated by the Planner node.",
    )
    memory_context: list[str] = Field(
        default_factory=list,
        description="Retrieved memory chunks injected into the LLM context.",
    )
    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list,
        description="Accumulated tool invocation records from the Executor node.",
    )
    critic_feedback: str | None = Field(
        default=None,
        description="Critique from the Critic node. If set, Executor may re-run.",
    )
    critic_iterations: int = Field(
        default=0,
        description="Number of Critic→Executor cycles. Guards against infinite loops.",
    )
    pending_approval: WSApprovalRequired | None = Field(
        default=None,
        description="Set when an action requires human approval before execution.",
    )
    final_response: str | None = Field(
        default=None,
        description="Aayra's final synthesized response. Set by the Responder node.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the graph terminated abnormally.",
    )
    tokens_used: int = 0
    start_time: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_approval_consistency(self) -> "AgentState":
        if self.status == AgentStatus.AWAITING_APPROVAL and self.pending_approval is None:
            raise ValueError(
                "AgentState.status is AWAITING_APPROVAL but pending_approval is not set."
            )
        return self

    @property
    def latency_ms(self) -> float:
        """Wall-clock time from graph entry to now, in milliseconds."""
        delta = datetime.utcnow() - self.start_time
        return delta.total_seconds() * 1000