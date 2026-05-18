"use client";

import { useState } from "react";
import { Send, Bot, User, Loader2 } from "lucide-react";
import { sendMessage } from "../lib/api";

export default function Home() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<{role: string, content: string}[]>([
    { role: "assistant", content: "Namaste Pavan! Main Aayra hoon. Aaj main tumhari kya help kar sakti hoon? ✨" }
  ]);
  const [loading, setLoading] = useState(false);
  
  const [sessionId] = useState("session-" + Math.random().toString(36).substring(7));

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMsg = input;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);

    try {
      const res = await sendMessage(userMsg, sessionId);
      setMessages((prev) => [...prev, { role: "assistant", content: res.content }]);
    } catch (error) {
      setMessages((prev) => [...prev, { role: "assistant", content: "Oops! API se connect nahi ho paaya. Backend chalu hai na?" }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-4 bg-linear-to-br from-slate-900 via-purple-900 to-slate-900 font-sans">
      
      <div className="w-full max-w-4xl h-[85vh] flex flex-col bg-white/5 backdrop-blur-xl border border-white/10 rounded-3xl shadow-2xl overflow-hidden">
        
        <div className="flex items-center p-6 border-b border-white/10 bg-white/5">
          <div className="w-12 h-12 rounded-full bg-purple-500/20 flex items-center justify-center border border-purple-500/50">
            <Bot className="w-7 h-7 text-purple-300" />
          </div>
          <div className="ml-4">
            <h1 className="text-xl font-bold text-white tracking-wide">Aayra</h1>
            <p className="text-sm text-purple-300 font-medium">Your Digital Dost</p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-6 scroll-smooth">
          {messages.map((msg, idx) => (
            <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`flex items-start max-w-[80%] ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`}>
                
                <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${msg.role === "user" ? "bg-blue-500/20 border border-blue-500/50 ml-3" : "bg-purple-500/20 border border-purple-500/50 mr-3"}`}>
                  {msg.role === "user" ? <User className="w-4 h-4 text-blue-300" /> : <Bot className="w-4 h-4 text-purple-300" />}
                </div>
                
                <div className={`p-4 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${msg.role === "user" ? "bg-blue-600/40 text-blue-50 rounded-tr-none border border-blue-500/30 shadow-[0_0_15px_rgba(37,99,235,0.2)]" : "bg-white/10 text-gray-100 rounded-tl-none border border-white/10 shadow-[0_0_15px_rgba(255,255,255,0.05)]"}`}>
                  {msg.content}
                </div>
              </div>
            </div>
          ))}
          
          {loading && (
            <div className="flex justify-start">
              <div className="flex items-center space-x-3 bg-white/5 p-4 rounded-2xl rounded-tl-none border border-white/10">
                <Loader2 className="w-5 h-5 text-purple-400 animate-spin" />
                <span className="text-sm text-purple-300 animate-pulse">Aayra soch rahi hai...</span>
              </div>
            </div>
          )}
        </div>

        <div className="p-6 border-t border-white/10 bg-black/20">
          <form onSubmit={(e) => { e.preventDefault(); handleSend(); }} className="flex gap-4">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Aayra se kuch pucho..."
              className="flex-1 bg-white/5 border border-white/10 rounded-xl px-6 py-4 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-purple-500/50 transition-all"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-purple-600 hover:bg-purple-500 text-white px-8 py-4 rounded-xl font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shadow-[0_0_20px_rgba(147,51,234,0.3)] hover:shadow-[0_0_25px_rgba(147,51,234,0.5)]"
            >
              <Send className="w-5 h-5" />
              Send
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}