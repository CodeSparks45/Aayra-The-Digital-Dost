import os
import google.generativeai as genai
from dotenv import load_dotenv

# .env file se tumhari API key load karega
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key or api_key == "AIzaSyTumhariAsliGeminiKeyYahanDaalDo...":
    print("❌ Bhai, .env mein asli API key daal de pehle!")
else:
    genai.configure(api_key=api_key)
    print("✅ Tumhari API key pe ye models available hain:\n")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"👉 {m.name}")
    except Exception as e:
        print(f"❌ Error aaya: {e}")