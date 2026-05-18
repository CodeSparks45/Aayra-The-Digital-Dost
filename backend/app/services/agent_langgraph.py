from app.models.schemas import AgentState, AgentStatus

async def process_user_message(state: AgentState) -> AgentState:
    # Ye ek temporary test response hai check karne ke liye ki API chal rahi hai ya nahi
    state.final_response = "Namaste Vedant! Main Aayra hoon. Tumhara production backend makkhan ki tarah chal raha hai! 🔥"
    state.status = AgentStatus.COMPLETED
    return state