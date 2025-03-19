// src/App.jsx
import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import { MessageCircle, Mic, MicOff, Send, Volume2, VolumeX } from 'lucide-react';

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState('checking');
  
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const messagesEndRef = useRef(null);
  
  // With Vite's proxy configuration, we can use relative URLs
  const API_URL = ''; // Empty string means it will use the proxy defined in vite.config.js

  // Check API status on component mount
  useEffect(() => {
    const checkApiStatus = async () => {
      try {
        const response = await fetch(`${API_URL}/api/status`);
        const data = await response.json();
        setApiStatus(data.models_loaded ? 'ready' : 'loading');
      } catch (error) {
        console.error('Error connecting to API:', error);
        setApiStatus('error');
      }
    };
    
    checkApiStatus();
    // Poll API status every 5 seconds if models are still loading
    const interval = setInterval(() => {
      if (apiStatus === 'loading') {
        checkApiStatus();
      } else {
        clearInterval(interval);
      }
    }, 5000);
    
    return () => clearInterval(interval);
  }, [apiStatus]);

  useEffect(() => {
    // Scroll to bottom of messages
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async () => {
    if (input.trim() === '') return;
    
    const userMessage = input;
    setInput('');
    
    // Add user message to chat
    setMessages(prev => [...prev, { text: userMessage, sender: 'user' }]);
    
    // Show loading indicator
    setIsLoading(true);
    
    try {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: userMessage,
          conversation_id: conversationId
        }),
      });
      
      const data = await response.json();
      
      if (data.error) {
        throw new Error(data.error);
      }
      
      // Update conversation ID
      if (data.conversation_id) {
        setConversationId(data.conversation_id);
      }
      
      // Add bot response to chat
      setMessages(prev => [...prev, { text: data.response, sender: 'bot' }]);
      
      // Convert response to speech if audio is enabled
      if (!isMuted) {
        await speakText(data.response);
      }
    } catch (error) {
      console.error('Error sending message:', error);
      setMessages(prev => [...prev, { 
        text: 'Sorry, there was an error processing your message. Please try again.',
        sender: 'bot',
        error: true
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      
      mediaRecorderRef.current = new MediaRecorder(stream);
      audioChunksRef.current = [];
      
      mediaRecorderRef.current.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };
      
      mediaRecorderRef.current.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        await sendAudioMessage(audioBlob);
        
        // Stop all audio tracks
        stream.getTracks().forEach(track => track.stop());
      };
      
      mediaRecorderRef.current.start();
      setIsRecording(true);
    } catch (error) {
      console.error('Error starting recording:', error);
      alert('Could not access microphone. Please check your browser permissions.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const sendAudioMessage = async (audioBlob) => {
    setIsLoading(true);
    
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob);
      
      if (conversationId) {
        formData.append('conversation_id', conversationId);
      }
      
      const response = await fetch(`${API_URL}/api/conversation-with-audio`, {
        method: 'POST',
        body: formData,
      });
      
      const data = await response.json();
      
      if (data.error) {
        throw new Error(data.error);
      }
      
      // Update conversation ID
      if (data.conversation_id) {
        setConversationId(data.conversation_id);
      }
      
      // Add user message to chat
      setMessages(prev => [...prev, { text: data.user_message, sender: 'user' }]);
      
      // Add bot response to chat
      setMessages(prev => [...prev, { 
        text: data.text_response, 
        sender: 'bot',
        audioUrl: data.audio_url ? `${API_URL}${data.audio_url}` : null
      }]);
      
      // Play audio if not muted
      if (!isMuted && data.audio_url) {
        const audio = new Audio(`${API_URL}${data.audio_url}`);
        audio.play();
      }
    } catch (error) {
      console.error('Error sending audio message:', error);
      setMessages(prev => [...prev, { 
        text: 'Sorry, there was an error processing your audio. Please try again.',
        sender: 'bot',
        error: true
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const speakText = async (text) => {
    try {
      const response = await fetch(`${API_URL}/api/text-to-speech`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ text }),
      });
      
      const data = await response.json();
      
      if (data.error) {
        throw new Error(data.error);
      }
      
      if (data.audio_url) {
        const audio = new Audio(`${API_URL}${data.audio_url}`);
        audio.play();
      }
    } catch (error) {
      console.error('Error converting text to speech:', error);
    }
  };

  const toggleMute = () => {
    setIsMuted(!isMuted);
  };

  const clearChat = () => {
    setMessages([]);
    if (conversationId) {
      // Optional: Delete conversation on server
      fetch(`${API_URL}/api/conversations/${conversationId}`, {
        method: 'DELETE'
      }).catch(err => console.error('Error deleting conversation:', err));
    }
    setConversationId('');
  };

  // Render API status screen if not ready
  if (apiStatus === 'checking' || apiStatus === 'loading') {
    return (
      <div className="app-container loading-screen">
        <div className="loading-content">
          <MessageCircle size={48} />
          <h2>AI Chatbot</h2>
          <p>{apiStatus === 'checking' ? 'Connecting to the server...' : 'Loading AI models...'}</p>
          <div className="loading-indicator">
            <div className="loading-dot"></div>
            <div className="loading-dot"></div>
            <div className="loading-dot"></div>
          </div>
        </div>
      </div>
    );
  }

  // Render error screen if API can't be reached
  if (apiStatus === 'error') {
    return (
      <div className="app-container error-screen">
        <div className="error-content">
          <MessageCircle size={48} />
          <h2>Connection Error</h2>
          <p>Could not connect to the chatbot server. Please check if the server is running.</p>
          <button onClick={() => setApiStatus('checking')}>Retry Connection</button>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <h1><MessageCircle size={24} /> AI Chatbot</h1>
        <div className="header-controls">
          <button 
            className="icon-button"
            onClick={toggleMute} 
            title={isMuted ? "Unmute" : "Mute"}
          >
            {isMuted ? <VolumeX size={20} /> : <Volume2 size={20} />}
          </button>
          <button className="clear-button" onClick={clearChat}>
            Clear Chat
          </button>
        </div>
      </header>

      <div className="messages-container">
        {messages.length === 0 && (
          <div className="empty-state">
            <MessageCircle size={48} />
            <p>Start a conversation with the AI assistant</p>
            <p className="empty-state-subtitle">Type a message or use the microphone to speak</p>
          </div>
        )}
        
        {messages.map((message, index) => (
          <div 
            key={index} 
            className={`message ${message.sender === 'user' ? 'user-message' : 'bot-message'} ${message.error ? 'error-message' : ''}`}
          >
            <div className="message-content">
              <p>{message.text}</p>
              
              {message.sender === 'bot' && message.audioUrl && !isMuted && (
                <button 
                  className="play-audio-button"
                  onClick={() => {
                    const audio = new Audio(message.audioUrl);
                    audio.play();
                  }}
                >
                  <Volume2 size={16} /> Play audio
                </button>
              )}
            </div>
          </div>
        ))}
        
        {isLoading && (
          <div className="message bot-message loading-message">
            <div className="loading-indicator">
              <div className="loading-dot"></div>
              <div className="loading-dot"></div>
              <div className="loading-dot"></div>
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="Type a message..."
          disabled={isRecording || isLoading}
          rows={1}
        />
        
        <div className="input-buttons">
          <button
            className={`record-button ${isRecording ? 'recording' : ''}`}
            onClick={isRecording ? stopRecording : startRecording}
            disabled={isLoading}
            title={isRecording ? "Stop recording" : "Start recording"}
          >
            {isRecording ? <MicOff size={20} /> : <Mic size={20} />}
          </button>
          
          <button
            className="send-button"
            onClick={handleSendMessage}
            disabled={isRecording || isLoading || input.trim() === ''}
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
}

export default App;