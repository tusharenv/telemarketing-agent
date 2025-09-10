from datetime import datetime
import signal
import sys
import os
from types import FrameType
import json
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    Request,
    WebSocketException,
    Depends,
    Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse
from bot import run_bot
from utils.logging import logger
from twilio.rest import Client
from dotenv import load_dotenv
from sqlmodel import select, Session
from contextlib import asynccontextmanager
# from db import Agent, Hubspot, get_session
from typing import Annotated, Any, Dict, List, Optional
from helper import Appointment_Prompt
import requests
from twilio.twiml.voice_response import VoiceResponse
from config import settings

load_dotenv(override=True)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_db_and_tables()
    yield
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")


# @app.get("/")
# async def hello(session: SessionDep) -> dict:
#     try:
#         return { "message": "Successfully running Cat." }
#     except Exception as e:
#         print("Failed to get / route.............")

@app.post("/agent")
async def agent(
    request: Request
    ):
    try:
        logger.debug(f"Request: {request}")
        logger.info("Handling TwiML agent request")

        # Play your greeting audio before connecting to agent
        greeting_url = f"https://{settings.HOST}/static/twiml_greeting.mp3"
        
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Play>{greeting_url}</Play>
                <Connect>
                    <Stream url="wss://{settings.HOST}/ws"></Stream>
                </Connect>
                <Say>The bot connection has been terminated.</Say>
            </Response>"""
        return HTMLResponse(content=twiml, media_type="application/xml")
    except Exception as e:
        logger.error(f"Failed to make call using agent: {e}")
        return create_error_twiml("Sorry, there was an error connecting to the agent.")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        logger.info("WebSocket connection initiated")
        await websocket.accept()
        start_data = websocket.iter_text()
        await start_data.__anext__()
        call_data = json.loads(await start_data.__anext__())
        call_data_start = call_data["start"]
        logger.info("WebSocket connection accepted")
        twilio = Client(
            call_data_start["accountSid"],
            os.getenv("TWILIO_AUTH_TOKEN"),
        )
        call_sid = call_data_start["callSid"]
        twilio.calls(call_sid).recordings.create()
        logger.info("Started recording calls")
 
        # the prompt that the agent will use
        prompt = Appointment_Prompt

        await run_bot(
            websocket,
            call_data_start["streamSid"],
            prompt,
        )            
        logger.info("Bot run completed successfully")
    except Exception as e:
        logger.error(f"Failed to make call to AI chatbot: {e}")
        await websocket.close()

class AnalyzeCallRequest(BaseModel):
    questions: Optional[List[Dict[str, Any]]] = None  
    agent_id: Optional[int] = None

class InitiateCallRequest(BaseModel):
    to_number: str

@app.post("/initiate-call")
async def initiate_call(
    request: InitiateCallRequest,
    req: Request
):
    """
    Initiate an outbound call from your Twilio number to a target phone number.
    """
    try:
        # Initialize Twilio client
        twilio_client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        
        # Use the request host to build webhook URL
        webhook_url = f"https://{settings.HOST}/agent"
        
        # Create the outbound call
        call = twilio_client.calls.create(
            to=request.to_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=webhook_url,
            method="POST"
        )
        
        return {
            "message": "Call initiated successfully",
            "call_sid": call.sid,
            "to_number": request.to_number,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Failed to initiate call: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate call: {str(e)}")


def create_error_twiml(message: str) -> HTMLResponse:
    """Create a TwiML error response"""
    response = VoiceResponse()
    response.say(message)
    response.hangup()
    return HTMLResponse(content=str(response), media_type="application/xml")

def shutdown_handler(signal_int: int, frame: FrameType) -> None:
    logger.info(f"Caught Signal {signal.strsignal(signal_int)}")
    from utils.logging import flush
    flush()
    # Safely exit program
    sys.exit(0)
if __name__ == "__main__":
    # Running application locally, outside of a Google Cloud Environment
    # handles Ctrl-C termination
    signal.signal(signal.SIGINT, shutdown_handler)
    uvicorn.run(app, host="0.0.0.0", port=8080)
else:
    # handles Cloud Run container termination
    signal.signal(signal.SIGTERM, shutdown_handler)