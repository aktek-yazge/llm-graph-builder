import os
import logging
import json
from datetime import datetime, timezone
# google.cloud.logging is optional - only needed if GCP logging is configured
try:
    from google.cloud import logging as gclogger
except (ImportError, ModuleNotFoundError):
    gclogger = None  # GCP logging is optional

class CustomLogger:
    def __init__(self):
        self.is_gcp_log_enabled = os.environ.get("GCP_LOG_METRICS_ENABLED", "False").lower() in ("true", "1", "yes")
        
        # Initialize GCP logging if enabled
        if self.is_gcp_log_enabled and gclogger is not None:
            try:
                self.logging_client = gclogger.Client()
                self.logger_name = "llm_experiments_metrics"
                self.gcp_logger = self.logging_client.logger(self.logger_name)
            except Exception as e:
                logging.warning(f"⚠️ GCP logging initialization failed: {e}")
                self.gcp_logger = None
        else:
            self.gcp_logger = None
            
        # Always initialize Python logging for OpenTelemetry integration
        self.python_logger = logging.getLogger('api_requests')

    def log_struct(self, message, severity="DEFAULT"):
        """
        Enhanced log_struct that works with both GCP and OpenTelemetry logging
        """
        if self.is_gcp_log_enabled and self.gcp_logger and message is not None:
            # Send to GCP if enabled
            self.gcp_logger.log_struct({"message": message, "severity": severity})
        
        # Always send to Python logging (for OpenTelemetry integration)
        if message is not None:
            # Convert message to structured format for better logging
            if isinstance(message, dict):
                # Extract common fields for better dashboard filtering
                api_name = message.get('api_name', 'unknown_api')
                elapsed_time = message.get('elapsed_api_time', '0')
                
                # Create formatted log message
                log_message = f"🚀 API {api_name} completed in {elapsed_time}s"
                
                # Filter out reserved logging fields to avoid conflicts
                reserved_fields = {
                    'message', 'levelname', 'name', 'msg', 'args', 'levelno', 
                    'pathname', 'filename', 'module', 'lineno', 'funcName', 
                    'created', 'msecs', 'relativeCreated', 'thread', 'threadName', 
                    'processName', 'process', 'stack_info', 'exc_info', 'exc_text'
                }
                
                # Create safe extra fields, renaming conflicts
                safe_extra_fields = {}
                for k, v in message.items():
                    if k in reserved_fields:
                        # Rename conflicting fields with prefix
                        safe_extra_fields[f'custom_{k}'] = v
                    else:
                        safe_extra_fields[k] = v
                
                # Use Python logging with extra fields for OpenTelemetry
                log_level = getattr(logging, severity.upper(), logging.INFO)
                self.python_logger.log(log_level, log_message, extra={
                    'component': 'api_endpoint',
                    'operation': api_name,
                    'api_name': api_name,
                    'elapsed_time': elapsed_time,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    **safe_extra_fields  # Include only safe fields
                })
            else:
                # Handle non-dict messages
                log_level = getattr(logging, severity.upper(), logging.INFO)
                self.python_logger.log(log_level, f"📝 {message}", extra={
                    'component': 'api_endpoint',
                    'operation': 'general_log',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
        else:
            # Fallback to print for backward compatibility
            print(f"[{severity}]{message}")
