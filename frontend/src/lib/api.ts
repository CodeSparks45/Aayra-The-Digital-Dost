import axios from 'axios';

// Tera FastAPI backend ka URL
const API_URL = 'http://127.0.0.1:8000/api/v1';

export const sendMessage = async (message: string, sessionId: string) => {
  try {
    const response = await axios.post(`${API_URL}/chat`, {
      user_id: "pavan123",
      session_id: sessionId,
      message: message,
      history: [],
      emotion_context: null,
      enable_tools: true
    });
    return response.data;
  } catch (error) {
    console.error("API Error:", error);
    throw error;
  }
};