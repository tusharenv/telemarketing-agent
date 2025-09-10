import asyncio
import os
import json
import time
import logging
from pipecat.frames.frames import EndFrame, LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.cartesia.stt import CartesiaSTTService
from pipecat.frames.frames import TextFrame, LLMTextFrame, TTSAudioRawFrame
from dotenv import load_dotenv
from config import settings
# import sys

load_dotenv(override=True)

# Configure logging for bot
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# only use it for debugging
# logger.add(sys.stderr, level="DEBUG")

async def run_bot(websocket_client, stream_sid, system_instruction):
    try:
        logger.info("Bot starting up...")
        bot_start_time = time.time()
        
        # Timing tracker for latency measurement
        timing_tracker = {
            'user_stopped_speaking': None,
            'bot_started_speaking': None,
            'user_speech_end': None,
            'llm_start': None,
            'llm_end': None,
            'tts_start': None,
            'audio_start': None
        }

        # Optimize transport for low latency
        transport = FastAPIWebsocketTransport(
            websocket=websocket_client,
            params=FastAPIWebsocketParams(
                audio_out_enabled=True,
                add_wav_header=False,
                vad_enabled=True,  
                vad_analyzer=SileroVADAnalyzer(
                    sample_rate=16000,  # Explicit sample rate
                    params=VADParams(
                        confidence_threshold=0.6,   
                        speech_pad_ms=200,           
                        silence_pad_ms=100          
                    )
                ),
                vad_audio_passthrough=True,
                serializer=TwilioFrameSerializer(stream_sid),
            ),
        )

        # Use faster model for lower latency
        llm = OpenAILLMService(
            api_key=settings.OPENAI_API_KEY,
            model="gpt-4o-mini"  
        )

        # Initialize STT service with optimized settings
        # stt = DeepgramSTTService(
        #     api_key=os.getenv("DEEPGRAM_API_KEY"),
        #     model="nova-3",
        #     detect_language=True
        # )
        
        # Optimize STT for lower latency
        stt = CartesiaSTTService(
            api_key=settings.CARTESIA_API_KEY,
            language="en",  # Specify language for faster processing
            model="sonic"   # Fastest Cartesia model
        )

        # Initialize TTS service optimized for speed and low latency
        tts = ElevenLabsTTSService(
            api_key=settings.ELEVENLABS_API_KEY, 
            voice_id="IKne3meq5aSn9XLyUdCD",
            model="eleven_turbo_v2",
            params=ElevenLabsTTSService.InputParams(
                stability=0.8,       
                similarity_boost=0.8, 
                use_speaker_boost=False,  
                speed=0.95,          
                style=0.0,          
                optimize_streaming_latency=3  
            )
        )

        # Optimized system prompt for faster processing (shorter = faster)
        system_prompt = system_instruction + """
            VOICE: Sound natural with Australian expressions. Use "hmm", "um", "well" naturally. Keep responses conversational and genuine.
            ALWAYS sound like a real Aussie having a genuine conversation.
        """
        
        messages = [{"role": "system", "content": system_prompt}]

        context = OpenAILLMContext(messages=messages)
        context_aggregator = llm.create_context_aggregator(context)

        tma_in = context_aggregator.user()
        tma_out = context_aggregator.assistant()

        pipeline = Pipeline(
            [
                transport.input(), 
                stt,  
                tma_in, 
                llm, 
                tts,  
                transport.output(),  
                tma_out, 
            ]
        )

        logger.info("Pipeline setup completed")

        # Optimize pipeline task for low latency
        task = PipelineTask(
            pipeline, params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=False,          # Disable metrics for speed
                enable_usage_metrics=False,    # Disable usage metrics
                send_initial_empty_metrics=False  # Skip initial metrics
            )
        )
        
        # Add simple timing using existing debug patterns
        import logging
        import sys
        
        # Create a custom logger to intercept debug messages
        class TimingLogHandler(logging.Handler):
            def __init__(self, timing_tracker):
                super().__init__()
                self.timing_tracker = timing_tracker
                
            def emit(self, record):
                message = record.getMessage()
                current_time = time.time()
                
                # Detect user stopped speaking from debug logs
                if "User stopped speaking" in message:
                    self.timing_tracker['user_stopped_speaking'] = current_time
                    logger.debug(f"User stopped speaking at {current_time:.3f}")
                    
                # Detect bot started speaking from debug logs  
                elif "Bot started speaking" in message:
                    self.timing_tracker['bot_started_speaking'] = current_time
                    logger.debug(f"Bot started speaking at {current_time:.3f}")
                    
                    # Calculate delay
                    if self.timing_tracker.get('user_stopped_speaking'):
                        delay = current_time - self.timing_tracker['user_stopped_speaking']
                        logger.info(f"RESPONSE DELAY: {delay:.3f}s (User stopped → Bot started)")
                        logger.info(f"{'EXCELLENT' if delay < 0.5 else 'GOOD' if delay < 1.0 else 'ACCEPTABLE' if delay < 1.5 else 'NEEDS IMPROVEMENT'} - Target: <1.0s")
                        logger.info("=" * 60)
                        
                        # Reset for next interaction
                        self.timing_tracker['user_stopped_speaking'] = None
                        self.timing_tracker['bot_started_speaking'] = None
        
        # Add our timing handler to the pipecat logger
        pipecat_logger = logging.getLogger('pipecat')
        timing_handler = TimingLogHandler(timing_tracker)
        pipecat_logger.addHandler(timing_handler)
        
        # Simple frame handler for transcripts
        task.set_reached_upstream_filter((TextFrame,))
        
        @task.event_handler("on_frame_reached_upstream") 
        async def _on_user_transcript(task, frame):
            if isinstance(frame, TextFrame) and frame.role == "user":
                logger.debug(f"User said: '{frame.text}'")
                
                # Send transcript to client
                payload = {
                    "type": "transcript", 
                    "text": frame.text,
                    "timestamp": getattr(frame, "timestamp", None),
                }
                await websocket_client.send_text(json.dumps(payload))

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            connection_time = time.time() - bot_start_time
            logger.info(f"Client connected in {connection_time:.3f}s - Starting conversation...")
            try:
                await task.queue_frames([LLMMessagesFrame(messages)])
            except Exception as e:
                logger.error(f"Failed to start conversation: {e}")

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected")
            try:
                messages_to_upload = tma_out.messages
                if len(tma_in.messages) > len(tma_out.messages):
                    messages_to_upload = tma_in.messages
                # the transcriptions are in messages_to_upload
                await task.queue_frames([EndFrame()])
            except Exception as e:
                logger.error(f"Error in client disconnect: {e}")

        runner = PipelineRunner(handle_sigint=False)
        logger.info("Pipeline ready - waiting for calls...")
        await runner.run(task)
        logger.info("Bot completed successfully")
    except Exception as e:
        logger.error(f"Bot failed: {e}")
