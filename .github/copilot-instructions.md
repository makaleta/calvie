# Calvie - iCal Viewer

Calvie is a FastAPI-based Python web application that provides a simple interface for viewing iCal events. It renders calendar data as HTML iframes and JSON endpoints, supporting multiple calendars configured via config.ini.

**ALWAYS reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.**

## Working Effectively

### Prerequisites and Environment Setup
- Python 3.12+ is required (tested and verified with Python 3.12.3)
- Install Poetry for dependency management:
  ```bash
  pip3 install poetry  # Takes ~30 seconds
  ```
- Verify Poetry installation:
  ```bash
  poetry --version  # Should show Poetry (version 2.1.4) or later
  ```

### Bootstrap, Build, and Test the Repository
- Install all dependencies:
  ```bash
  poetry install  # Takes ~5 seconds. NEVER CANCEL - this is fast.
  ```
- Run the comprehensive test suite:
  ```bash
  poetry run pytest -v  # Takes <1 second - 14 tests. NEVER CANCEL.
  ```
- Alternative test command:
  ```bash
  poetry run pytest  # Without verbose output, takes <1 second
  ```

### Configuration Setup
Create a `config.ini` file in the repository root before running the application:
```ini
[DEFAULT]
timezone = UTC
days to future = 40
locale = en_GB
width = 300

[example_calendar]
url = https://example.com/calendar.ics
```

**Note**: In environments with network restrictions, use local .ics files for testing:
```ini
[testcal]
url = file:///absolute/path/to/calendar.ics
```

### Running the Application
- Start the FastAPI development server:
  ```bash
  poetry run uvicorn main:app --host 127.0.0.1 --port 8000
  ```
  Startup takes ~1 second. Access at http://127.0.0.1:8000
- Alternative with auto-reload for development:
  ```bash
  poetry run uvicorn main:app --reload --host 127.0.0.1 --port 8000
  ```

### Docker Support
- **Build Docker image** (Note: May fail in restricted network environments):
  ```bash
  docker build -t calvie .  # Takes 4-5 minutes if network allows. NEVER CANCEL.
  ```
- **Run Docker container** (if build succeeds):
  ```bash
  docker run -p 8080:8080 calvie
  ```
  Application will be available at http://localhost:8080

## Validation

### Manual Testing Scenarios
After making changes, ALWAYS test these key scenarios:

1. **Basic API functionality**:
   ```bash
   curl http://127.0.0.1:8000/  # Should return {"status": "I am a teapot at default"}
   ```

2. **Calendar data endpoint** (requires valid config.ini with working calendar URL):
   ```bash
   curl http://127.0.0.1:8000/cal/your_calendar_name
   ```
   **Note**: Returns {"detail": "No host specified."} or similar errors when calendar URLs are unreachable.

3. **Iframe endpoint** (renders HTML for calendar display):
   ```bash
   curl http://127.0.0.1:8000/iframe/your_calendar_name
   ```
   **Note**: Returns error.html template when calendar data cannot be fetched.

4. **Configuration validation**:
   - Ensure config.ini exists and contains valid calendar configurations
   - Test with both configured calendar names and direct .ics URLs
   - Example working test config for local development:
     ```ini
     [DEFAULT]
     timezone = UTC
     days to future = 40
     locale = en_GB
     width = 300
     
     [testcal]
     url = file:///path/to/test_calendar.ics
     ```

### Test Infrastructure
- **Run tests before committing**: `poetry run pytest -v`
- **Test structure**: All tests are in `tests/test_main.py` with 14 comprehensive test cases
- **Test categories**:
  - URL validation for .ics files
  - Root endpoint (HTTP 418 teapot response)
  - Calendar data retrieval
  - Iframe HTML rendering
  - Configuration handling
  - Date localization

### CI/CD Validation
The repository uses GitHub Actions:
- **Test workflow** (`.github/workflows/test.yml`): Runs on all PRs and pushes to master/develop
- **Docker workflow** (`.github/workflows/docker-image.yml`): Builds and publishes Docker images on master pushes
- **No linting tools configured** - no additional linting steps required

## Common Tasks

### Development Workflow
1. Make code changes
2. Run tests: `poetry run pytest -v`
3. Start application: `poetry run uvicorn main:app --reload`
4. Test endpoints manually
5. Commit changes

### Adding New Calendar Sources
1. Edit `config.ini` to add new calendar section:
   ```ini
   [new_calendar]
   url = https://example.com/new_calendar.ics
   timezone = UTC
   days to future = 30
   locale = en_GB
   width = 400
   ```
2. Restart the application to load new configuration
3. Test with: `curl http://127.0.0.1:8000/cal/new_calendar`

### Debugging
- **Application logs**: Check uvicorn output for request/response info
- **Error handling**: Application returns HTTP 418 for root, 404 for invalid calendars, 400 for calendar fetch errors
- **Template errors**: Check `templates/error.html` for error page rendering

## Project Structure

### Core Files
```
/home/runner/work/calvie/calvie/
├── main.py              # FastAPI application with 3 endpoints
├── config.ini           # Calendar configuration (create this)
├── pyproject.toml       # Poetry dependencies and project config
├── pytest.ini          # Test configuration
├── Dockerfile           # Multi-stage Docker build
├── templates/
│   ├── iframe.html      # Calendar display template
│   └── error.html       # Error page template
└── tests/
    ├── __init__.py
    └── test_main.py     # All 14 test cases
```

### Key Functions in main.py
- `root()`: Returns HTTP 418 teapot response
- `cal_data()`: Fetches and returns calendar data as JSON
- `iframe()`: Renders calendar data as HTML iframe
- `is_ics_url()`: Validates .ics URL format

### Important Configuration Notes
- **Default config values**: timezone=UTC, days to future=40, locale=en_GB, width=300
- **Calendar URL formats**: Supports HTTP/HTTPS URLs ending in .ics
- **Template variables**: iframe.html uses width, events, lang, force_scheme
- **Error handling**: Uses templates/error.html for error responses

### Dependencies
Main runtime dependencies from pyproject.toml:
- fastapi ^0.115.11
- uvicorn ^0.34.0  
- icalevents ^0.2.1
- jinja2 ^3.1.6
- babel ^2.17.0

Development dependencies:
- pytest ^8.4.1
- pytest-asyncio ^1.1.0
- httpx ^0.28.1

## Limitations and Known Issues

### Network Dependencies
- **External calendar URLs**: Application requires internet access to fetch external .ics files
- **Docker build**: May fail in restricted network environments due to Poetry installation step
- **Testing with external URLs**: Use local .ics files or file:// URLs for testing in isolated environments

### Configuration Reload
- **Runtime config changes**: Requires application restart to pick up config.ini changes
- **No hot reload**: Configuration is loaded once at startup

### Build and Deploy
- **No linting tools**: No automatic code formatting or linting configured
- **Manual testing required**: No automated functional tests for UI components
- **Docker networking**: Container exposes port 8080, not 8000 like development server

ALWAYS test your changes with the validation scenarios above and run the test suite before committing code.

## Quick Reference - Common Commands

### Essential Commands (Copy-Paste Ready)
```bash
# Setup and dependencies
poetry install                                    # ~5 seconds
poetry run pytest -v                             # <1 second

# Development server
poetry run uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Testing and validation
curl http://127.0.0.1:8000/                      # Test root endpoint
poetry run pytest                                # Run all tests
```

### Timing Expectations
- **Poetry install**: ~5 seconds - NEVER CANCEL
- **Test suite**: <1 second for all 14 tests - NEVER CANCEL
- **Server startup**: ~1 second
- **Docker build**: 4-5 minutes (may fail due to network restrictions) - NEVER CANCEL

All validated commands work reliably and should be used exactly as documented.