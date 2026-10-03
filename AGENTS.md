# AGENTS.md - Guidelines for Agentic Coding in ADS-L Live Map

This document provides guidelines for coding agents working on the ADS-L Live Map project.

## Project Overview

ADS-L Live Map is a Flask application that displays real-time positions of ADS-L equipped aircraft worldwide. It connects to the Open Glider Network (OGN) APRS feed, processes telemetry data, and visualizes active devices on an interactive map.

## Build/Lint/Test Commands

### Environment Setup
```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Application

**For Development/Testing:**
```bash
gunicorn -w 1 -b 127.0.0.1:5000 --reload --log-level debug --capture-output app:app
```

**For Production:**
```bash
gunicorn -w 1 --threads 2 -k gthread --timeout 0 -b 127.0.0.1:5000 app:app
```

**Flask Development Mode:**
```bash
export FLASK_APP=app.py
export FLASK_ENV=development
flask run
```

### Testing

There are no formal test files in the project. Testing is done manually by:
1. Running the application in development mode
2. Accessing the endpoints:
   - `/ads-l-map` - Main map interface
   - `/ads-l/` - JSON data of active devices
   - `/ads-l/stats` - Monthly statistics
   - `/device-map` - Device type mapping
3. Verifying data appears correctly on the map
4. Checking logs for errors

### Linting

The project doesn't have specific linting tools configured. Use standard Python practices:
```bash
# Check for syntax errors
python -m py_compile app.py

# Use flake8 for basic linting (if installed)
flake8 app.py
```

## Code Style Guidelines

### General Principles
- Follow existing code patterns and conventions
- Keep code simple and readable
- Use descriptive variable and function names
- Add comments only when necessary for clarity
- Maintain consistency with existing codebase

### Imports
- Group imports by type: standard library, third-party, local
- Sort imports alphabetically within each group
- Use absolute imports
- Avoid wildcard imports (`from module import *`)

**Example:**
```python
import socket
import threading
import time
import datetime
import re
import logging
import sys
from flask import Flask, jsonify, render_template
from threading import Thread
import pymysql
import csv
import requests
import io
from io import StringIO
from dotenv import load_dotenv
import os
```

### Formatting
- Use 4 spaces for indentation (no tabs)
- Limit lines to 120 characters where reasonable
- Use spaces around operators and after commas
- Use consistent spacing in function calls and definitions
- Use blank lines to separate logical sections

### Naming Conventions
- **Variables and functions**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Classes**: `PascalCase` (not commonly used in this project)
- **Global variables**: `snake_case` with descriptive names
- **Thread functions**: Use `_thread` suffix (e.g., `ads_l_listener`)
- **Route functions**: Use descriptive names (e.g., `get_ads_l`)

### Error Handling
- Use try-except blocks for operations that may fail
- Log errors with appropriate severity levels
- Provide meaningful error messages
- Handle specific exceptions when possible
- Use retry logic for transient failures (database connections, network requests)

**Example:**
```python
def get_db_connection(max_retries=30, retry_delay=10):
    retries = 0
    while retries < max_retries:
        try:
            conn = pymysql.connect(
                host="localhost",
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database="ads_l",
                autocommit=True
            )
            main_logger.info("Connection to database established.")
            return conn
        except pymysql.MySQLError as e:
            retries += 1
            main_logger.error(f"Error connecting database (attempt {retries}/{max_retries}): {e}")
            if retries < max_retries:
                main_logger.info(f"New attempt in {retry_delay} seconds...")
                time.sleep(retry_delay)
    raise Exception("Cannot connect to database after many attempts.")
```

### Logging
- Use the `main_logger` for application logging
- Log levels:
  - `DEBUG`: Detailed debugging information (disabled by default)
  - `INFO`: General status messages
  - `ERROR`: Error conditions
  - `WARNING`: Warning conditions
- Include relevant context in log messages
- Avoid excessive logging that could impact performance

### Threading
- Use daemon threads for background tasks
- Start threads in the `bootstrap()` function
- Use appropriate sleep intervals to avoid busy waiting
- Handle thread exceptions gracefully
- Use thread-safe operations for shared data

### Database Operations
- Use connection pooling where possible
- Implement retry logic for database operations
- Use parameterized queries to prevent SQL injection
- Close connections properly on application exit
- Use appropriate transaction isolation levels

### API Design
- Use RESTful principles for endpoints
- Return JSON responses for data endpoints
- Use appropriate HTTP status codes
- Keep endpoints simple and focused
- Document endpoints in the README

### Security
- Use environment variables for sensitive data
- Validate all external inputs
- Implement proper error handling
- Use secure defaults
- Follow principle of least privilege

### Documentation
- Keep README up to date
- Document major changes
- Include usage examples
- Document API endpoints
- Explain configuration options

## Development Workflow

1. **Understand the requirement**: Read the issue or feature request carefully
2. **Analyze existing code**: Look at similar functionality in the codebase
3. **Plan the implementation**: Break down the task into smaller steps
4. **Implement changes**: Follow code style guidelines
5. **Test manually**: Run the application and verify the changes work
6. **Review code**: Check for consistency, errors, and edge cases
7. **Document changes**: Update README if necessary

## Common Patterns

### Background Threads
```python
def periodic_device_type_update(interval=3600):
    """Update the device type map every interval seconds."""
    while True:
        update_device_type_map()
        time.sleep(interval)

Thread(target=periodic_device_type_update, daemon=True).start()
```

### Data Processing
```python
def parse_aprs_line(line):
    try:
        # Parse logic here
        return parsed_data
    except Exception as e:
        main_logger.error("parse_aprs_line failed: %s", e)
        return None
```

### Error Handling with Retries
```python
def record_monthly_device(device_id, device_type):
    max_retries = 3
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            # Database operation
            break
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                main_logger.error("Cannot write to database after many attempts.")
```

## Agent-Specific Notes

- Always read existing code before making changes
- Follow the existing architecture and patterns
- Be cautious with global state (especially `ads_l_devices`)
- Test changes thoroughly before committing
- Document any significant changes or additions
- Keep the codebase clean and maintainable
