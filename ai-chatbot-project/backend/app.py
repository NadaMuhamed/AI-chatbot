from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import tempfile
import uuid
import logging
from pathlib import Path
import time
from functools import wraps
from typing import Dict, Any, Optional
import threading
import asyncio
from transformers import pipeline, Conversation
import scipy.io.wavfile as wav
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
class Config:
    TEMP_DIR = Path(tempfile.gettempdir()) / "chatbot_audio"
    AUDIO_RETENTION_SECONDS = 3600  # Store audio files for 1 hour
    MODELS_DIR = Path("./models")
    DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "t")
    PORT = int(os.environ.get("PORT", 5000))
    
    # Model paths and configurations
    CONVERSATION_MODEL = MODELS_DIR / "facebook/blenderbot-400M-distill"
    ASR_MODEL = "distil-whisper/distil-small.en"
    TTS_MODEL = MODELS_DIR / "kakao-enterprise/vits-ljs"

# Create temp directory if it doesn't exist
Config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

# In-memory storage for conversation history
# In a production app, this would be a database
conversation_store: Dict[str, list] = {}
audio_file_store: Dict[str, Dict[str, Any]] = {}

# Load models in a separate thread to avoid blocking app startup
models_loaded = threading.Event()
models = {}

def load_models():
    logger.info("Loading AI models...")
    try:
        # Conversation model
        models["chatbot"] = pipeline(
            task="conversational",
            model=Config.CONVERSATION_MODEL
        )
        
        # Speech-to-text model
        models["asr"] = pipeline(
            task="automatic-speech-recognition",
            model=Config.ASR_MODEL
        )
        
        # Text-to-speech model
        models["narrator"] = pipeline(
            "text-to-speech",
            model=Config.TTS_MODEL
        )
        
        logger.info("All models loaded successfully!")
        models_loaded.set()
    except Exception as e:
        logger.error(f"Error loading models: {str(e)}")
        raise

# Start loading models in background
threading.Thread(target=load_models, daemon=True).start()

# Periodic cleanup of old audio files
def cleanup_old_files():
    while True:
        try:
            current_time = time.time()
            # Clean audio files
            for filename, info in list(audio_file_store.items()):
                if current_time - info["timestamp"] > Config.AUDIO_RETENTION_SECONDS:
                    file_path = info["path"]
                    try:
                        if os.path.exists(file_path):
                            os.unlink(file_path)
                        del audio_file_store[filename]
                        logger.debug(f"Cleaned up old audio file: {filename}")
                    except Exception as e:
                        logger.error(f"Error cleaning up file {filename}: {str(e)}")
            
            # Sleep for 5 minutes
            time.sleep(300)
        except Exception as e:
            logger.error(f"Error in cleanup thread: {str(e)}")
            time.sleep(60)  # Sleep for a minute if there's an error

# Start cleanup thread
threading.Thread(target=cleanup_old_files, daemon=True).start()

# Middleware to ensure models are loaded
def ensure_models_loaded(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not models_loaded.is_set():
            return jsonify({'error': 'Models are still loading. Please try again shortly.'}), 503
        return f(*args, **kwargs)
    return decorated_function

# Helper function for error handling
def api_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    return wrapper

@app.route('/api/status', methods=['GET'])
def status():
    """Check if the API is running and models are loaded"""
    return jsonify({
        'status': 'operational',
        'models_loaded': models_loaded.is_set()
    })

@app.route('/api/chat', methods=['POST'])
@ensure_models_loaded
@api_response
def chat():
    """
    Process text input and return a conversational response
    
    Request:
    {
        "message": "User message here",
        "conversation_id": "optional-conversation-id"
    }
    """
    data = request.json
    user_message = data.get('message', '')
    conversation_id = data.get('conversation_id', '')
    
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400
    
    # Create new conversation if needed
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        conversation_store[conversation_id] = []
    elif conversation_id not in conversation_store:
        conversation_store[conversation_id] = []
    
    # Get conversation history
    history = conversation_store[conversation_id]
    
    # Create conversation object with history
    convo = Conversation(user_message)
    for prev_msg in history:
        if prev_msg['role'] == 'user':
            convo.add_user_input(prev_msg['content'])
        else:
            convo.mark_processed()
            convo.append_response(prev_msg['content'])
    
    # Get response from model
    convo = models["chatbot"](convo)
    
    # Extract the assistant's response
    response = convo.generated_responses[-1] if convo.generated_responses else "I'm not sure how to respond to that."
    
    # Update conversation history
    history.append({'role': 'user', 'content': user_message})
    history.append({'role': 'assistant', 'content': response})
    conversation_store[conversation_id] = history
    
    return jsonify({
        'response': response,
        'conversation_id': conversation_id
    })

@app.route('/api/speech-to-text', methods=['POST'])
@ensure_models_loaded
@api_response
def speech_to_text():
    """
    Convert uploaded audio to text
    
    Request: multipart/form-data with an 'audio' file
    """
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    
    # Save the audio file temporarily
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav', dir=Config.TEMP_DIR)
    audio_file.save(temp_file.name)
    temp_file.close()
    
    # Process the audio file
    result = models["asr"](temp_file.name)
    
    # Clean up the temporary file
    os.unlink(temp_file.name)
    
    return jsonify({
        'text': result["text"]
    })

@app.route('/api/text-to-speech', methods=['POST'])
@ensure_models_loaded
@api_response
def text_to_speech():
    """
    Convert text to speech audio
    
    Request:
    {
        "text": "Text to convert to speech"
    }
    """
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Generate speech from text
    result = models["narrator"](text)
    
    # Generate a unique filename
    filename = f"{uuid.uuid4()}.wav"
    file_path = Config.TEMP_DIR / filename
    
    # Save the audio data to the file
    wav.write(file_path, result["sampling_rate"], result["audio"][0])
    
    # Store file info
    audio_file_store[filename] = {
        "path": str(file_path),
        "timestamp": time.time(),
        "type": "audio/wav"
    }
    
    return jsonify({
        'audio_url': f"/api/audio/{filename}"
    })

@app.route('/api/conversation-with-audio', methods=['POST'])
@ensure_models_loaded
@api_response
def conversation_with_audio():
    """
    Combined endpoint for a complete audio conversation flow:
    1. Convert user's speech to text
    2. Process text with conversation model
    3. Convert response text to speech
    
    Request: multipart/form-data with an 'audio' file and optional 'conversation_id'
    """
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    conversation_id = request.form.get('conversation_id', '')
    
    # Save the audio file temporarily
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav', dir=Config.TEMP_DIR)
    audio_file.save(temp_file.name)
    temp_file.close()
    
    # 1. Convert speech to text
    speech_result = models["asr"](temp_file.name)
    user_message = speech_result["text"]
    
    # Clean up the temporary file
    os.unlink(temp_file.name)
    
    # 2. Process with conversation model
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        conversation_store[conversation_id] = []
    elif conversation_id not in conversation_store:
        conversation_store[conversation_id] = []
    
    # Get conversation history
    history = conversation_store[conversation_id]
    
    # Create conversation object with history
    convo = Conversation(user_message)
    for prev_msg in history:
        if prev_msg['role'] == 'user':
            convo.add_user_input(prev_msg['content'])
        else:
            convo.mark_processed()
            convo.append_response(prev_msg['content'])
    
    # Get response from model
    convo = models["chatbot"](convo)
    text_response = convo.generated_responses[-1] if convo.generated_responses else "I'm not sure how to respond to that."
    
    # Update conversation history
    history.append({'role': 'user', 'content': user_message})
    history.append({'role': 'assistant', 'content': text_response})
    conversation_store[conversation_id] = history
    
    # 3. Convert response text to speech
    speech_result = models["narrator"](text_response)
    
    # Generate a unique filename
    filename = f"{uuid.uuid4()}.wav"
    file_path = Config.TEMP_DIR / filename
    
    # Save the audio data to the file
    wav.write(file_path, speech_result["sampling_rate"], speech_result["audio"][0])
    
    # Store file info
    audio_file_store[filename] = {
        "path": str(file_path),
        "timestamp": time.time(),
        "type": "audio/wav"
    }
    
    return jsonify({
        'user_message': user_message,
        'text_response': text_response,
        'conversation_id': conversation_id,
        'audio_url': f"/api/audio/{filename}"
    })

@app.route('/api/audio/<filename>', methods=['GET'])
@api_response
def get_audio(filename):
    """
    Retrieve generated audio file by filename
    """
    if filename not in audio_file_store:
        return jsonify({'error': 'Audio file not found'}), 404
    
    file_info = audio_file_store[filename]
    file_path = file_info["path"]
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'Audio file not found on server'}), 404
    
    return send_file(
        file_path,
        mimetype=file_info["type"],
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/language-feedback', methods=['POST'])
@ensure_models_loaded
@api_response
def language_feedback():
    """
    Provide feedback on user's English language usage
    
    Request:
    {
        "text": "User's English text to analyze"
    }
    """
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Note: This is a placeholder. In a real implementation, 
    # you would use a language analysis model or service.
    
    # Example feedback (this should be replaced with actual model output)
    feedback = {
        'grammar_score': 8.5,  # Example score out of 10
        'vocabulary_score': 7.8,
        'fluency_score': 8.2,
        'suggestions': [
            'Consider using more varied sentence structures',
            'Your use of prepositions could be improved in some places',
            'You have good vocabulary, but could use more advanced connectors'
        ],
        'corrected_text': text  # In a real implementation, this would contain corrections
    }
    
    return jsonify(feedback)

@app.route('/api/conversations/<conversation_id>', methods=['GET'])
@api_response
def get_conversation_history(conversation_id):
    """
    Retrieve conversation history
    """
    if conversation_id not in conversation_store:
        return jsonify({'error': 'Conversation not found'}), 404
    
    return jsonify({
        'conversation_id': conversation_id,
        'messages': conversation_store[conversation_id]
    })

@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
@api_response
def delete_conversation(conversation_id):
    """
    Delete conversation history
    """
    if conversation_id in conversation_store:
        del conversation_store[conversation_id]
    
    return jsonify({
        'success': True,
        'message': 'Conversation deleted'
    })

if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=Config.PORT)