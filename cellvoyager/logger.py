import os
import datetime
import json
import logging
from logging.handlers import RotatingFileHandler


def _trim_text(text, limit=1000):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


class Logger:
    """Minimalist logger to track prompts and analysis outputs"""
    
    def __init__(self, analysis_name, log_dir="logs"):
        # Create log directory if it doesn't exist
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        # Create timestamp for this run
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"{analysis_name}_log_{timestamp}.log")
        self.trace_file = os.path.join(log_dir, f"{analysis_name}_trace_{timestamp}.log")
        logger_name = f"cellvoyager.{analysis_name}.{timestamp}"
        
        # Set up pretty logger
        self.logger = logging.getLogger(f"{logger_name}.pretty")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        # Set up structured trace logger
        self.trace_logger = logging.getLogger(f"{logger_name}.trace")
        self.trace_logger.setLevel(logging.INFO)
        self.trace_logger.propagate = False
        
        # Clear any existing handlers
        if self.logger.handlers:
            self.logger.handlers.clear()
        if self.trace_logger.handlers:
            self.trace_logger.handlers.clear()
        
        # Create file handler
        file_handler = RotatingFileHandler(
            self.log_file, 
            maxBytes=50*1024*1024,  # 50MB max file size
            backupCount=5
        )
        
        # Create formatter for clean, readable logs
        formatter = logging.Formatter(
            "\n\n" + "="*80 + "\n%(asctime)s - %(levelname)s\n" + 
            "="*80 + "\n%(message)s"
        )
        
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        trace_handler = RotatingFileHandler(
            self.trace_file,
            maxBytes=50*1024*1024,
            backupCount=5,
        )
        trace_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        self.trace_logger.addHandler(trace_handler)
        
        # Log initialization
        self.logger.info(f"Logging started. Log file: {self.log_file}")
        self.trace_logger.info(f"[trace_started] trace_file={self.trace_file}")
    
    def log_prompt(self, role, prompt_text, prompt_name=""):
        """Log a prompt sent to the model"""
        header = f"PROMPT: {prompt_name}" if prompt_name else "PROMPT"
        self.logger.info(f"{header} ({role})\n\n{prompt_text}")
        self.log_trace(
            "prompt_logged",
            f"role={role} prompt_name={prompt_name or 'prompt'} chars={len(prompt_text or '')} preview={_trim_text(prompt_text, 400)}",
        )
    
    def log_response(self, response_text, source="model"):
        """Log a response from the model or analysis output"""
        self.logger.info(f"RESPONSE/OUTPUT: {source}\n\n{response_text}")
        self.log_trace(
            "response_logged",
            f"source={source} chars={len(response_text or '')} preview={_trim_text(response_text, 400)}",
        )
    
    def log_code(self, code, iteration=None, analysis=None):
        """Log code generated or executed"""
        self.logger.info(f"CODE\n\n```python\n{code}\n```")
        self.log_trace(
            "code_logged",
            f"iteration={iteration} analysis={analysis} chars={len(code or '')} preview={_trim_text(code, 400)}",
        )

    def log_trace(self, event, text):
        """Write an observable execution/planning trace entry."""
        self.trace_logger.info(f"[{event}] {text}")

    def log_trace_json(self, event, payload):
        """Write an observable execution/planning trace entry as JSON."""
        self.log_trace(event, json.dumps(payload, ensure_ascii=False, default=str))
    
    def format_traceback(self, error_name, error_value, traceback):
        """Format error information for error messages"""
        return f"ERROR: {error_name}: {error_value}\n\n{traceback}"
    
    def log_error(self, error_msg, code=None):
        """Log critical errors that need investigation"""
        msg = f"ERROR\n\n{error_msg}"
        if code:
            msg += f"\n\nIn code:\n```python\n{code}\n```"
        self.logger.error(msg)
        self.log_trace(
            "error_logged",
            f"chars={len(error_msg or '')} preview={_trim_text(error_msg, 400)}",
        )
