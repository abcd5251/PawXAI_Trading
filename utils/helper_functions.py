import tiktoken 
import requests
import os
from dotenv import load_dotenv
load_dotenv()

COMPLETIONS_MODEL = "gpt-5-nano"

def num_tokens_from_string(string: str, encoding_name = COMPLETIONS_MODEL) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens