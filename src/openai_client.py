import json
from openai import OpenAI 
from dotenv import load_dotenv
import os

load_dotenv()

def get_client():
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return client

def upload_files(client, file_path):
    response = client.files.create(
        file=open(file_path, "rb"),
        purpose="assistants",
        expires_after={
            "anchor": "created_at",
            "seconds": 3600
        }   
    )
    return json.loads(response.json())['id']


def get_response_from_gpt(client, input_text, file_ids=[], model="gpt-5-nano", enable_web_search=False, label=""):
    print(f"[{label}] Starting request...")
    input_files = []
    tools = []

    if file_ids:
        input_files = [{"type": "input_file", "file_id": fid} for fid in file_ids]
    
    if enable_web_search:
        tools=[{"type": "web_search"}]

    response = client.responses.create(
        model=model,
        tools=tools,
        input=[
            {
                "role": "user",
                "content": [
                    *input_files,
                    {"type": "input_text", "text": input_text}
                ]
            }
        ]
    )
    print(f"[{label}] Finished request.")

    return response.output_text
