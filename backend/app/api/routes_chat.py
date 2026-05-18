from fastapi import APIRouter
from app.models.schemas import ChatRequest, ChatResponse, AgentState, EmotionAnalysis
from app.services.agent_langgraph import process_user_message

router = APIRouter(tags=["Chat"])

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # Request ko internal AgentState mein map karo
    initial_state = AgentState(
        user_id=request.user_id,
        session_id=request.session_id,
        user_message=request.message,
        chat_history=request.history,
        emotion_context=request.emotion_context or EmotionAnalysis(),
        enable_tools=request.enable_tools
    )
    
    # LangGraph Agent (Aayra ka dimaag) ko message bhejo
    final_state = await process_user_message(initial_state)
    
    # Frontend ko response bhejo
    return ChatResponse(
        session_id=final_state.session_id,
        content=final_state.final_response or "Main samajh nahi paayi, kya tum repeat karoge?",
        agent_status=final_state.status,
        tool_calls=final_state.tool_calls
    )