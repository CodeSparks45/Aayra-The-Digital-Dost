from app.models.schemas import AgentState, AgentStatus
from app.core.config import get_settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

settings = get_settings()

# ── 1. Initialize Gemini Model ──
# Ye LangChain ka wrapper hai jo Gemini Flash model ko setup karta hai
llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    temperature=settings.GEMINI_TEMPERATURE,
    google_api_key=settings.GOOGLE_API_KEY
)

async def process_user_message(state: AgentState) -> AgentState:
    try:
        # ── 2. Prompt Setup ──
        # SystemMessage = Aayra ki personality
        # HumanMessage = Jo tumne (Pavan ne) bheja
        messages = [
            SystemMessage(content="You are Aayra, a warm, friendly, and highly intelligent AI companion. The user's name is Pavan. Always reply in a mix of Hindi and English (Hinglish) with an energetic and helpful tone."),
            HumanMessage(content=state.user_message)
        ]
        
        # ── 3. Call Gemini (Async) ──
        response = await llm.ainvoke(messages)
        
        # ── 4. Set the Response ──
        state.final_response = response.content
        state.status = AgentStatus.COMPLETED
        
    except Exception as e:
        # Agar API key galat hui ya internet issue hua
        state.final_response = f"Oops Pavan! Mera dimaag (Gemini) theek se connect nahi ho paaya. Error: {str(e)}"
        state.status = AgentStatus.FAILED
        
    return state