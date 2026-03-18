# utils.py
# Copyright RomanAILabs - Daniel Harding
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import Any, Dict

def setup_logging(log_dir: str, verbose: bool = False) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger('romatask')
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    fh = RotatingFileHandler(os.path.join(log_dir, 'romatask.log'), maxBytes=5*1024*1024, backupCount=10)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

def get_safe_filename(prompt: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9\s]', '', prompt).strip().replace(' ', '_')
    return safe[:40] if safe else "romatask_project"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def call_ollama(model: str, prompt: str, format: str = "") -> Dict[str, Any]:
    import ollama
    if format:
        return ollama.generate(model=model, prompt=prompt, format=format)
    return ollama.generate(model=model, prompt=prompt)
