# Calvie - iCal Viewer Application

**ALWAYS follow these instructions first.** Only fall back to additional search and context gathering if the information here is incomplete or found to be in error.

Calvie is a Python FastAPI web application that serves as a simple iCal viewer. It displays calendar events from iCal (.ics) files in an HTML iframe format with localization support.

## Quick Start - Working Effectively

### Install Dependencies
```bash
# Install Poetry (if not available)
pip3 install poetry

# Install all dependencies - NEVER CANCEL, takes ~5 seconds, set timeout to 60+ seconds
poetry install --no-interaction --no-ansi

# Verify installation
poetry --version
```

### Running Tests
```bash
# Run full test suite - NEVER CANCEL, takes ~2 seconds, set timeout to 30+ seconds
poetry run pytest -v

# Run with verbose output for debugging
poetry run pytest -v -s
```

### Running the Application
```bash
# REQUIRED: Create config.ini file first (see Configuration section below)

# Run the FastAPI application
poetry run uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Application will be available at http://127.0.0.1:8000
```

## Essential Configuration

**CRITICAL:** The application requires a `config.ini` file in the project root. Create it with the following structure:

```ini
[DEFAULT]
timezone = Europe/London
days to future = 40
locale = en_GB
width = 355

[testcal]
url = https://example.com/calendar.ics
```

Without this file, the application will start but calendar endpoints will fail.

## Validation Scenarios

**ALWAYS perform these validation steps after making changes:**

1. **Basic Application Health:**
   ```bash
   # Start the application
   poetry run uvicorn main:app --host 127.0.0.1 --port 8000 &
   
   # Test the root endpoint (should return HTTP 418 "I am a teapot")
   curl -s http://127.0.0.1:8000/
   # Expected: {"status":"I am a teapot at default"}
   ```

2. **Test Suite Validation:**
   ```bash
   # Run all tests - should pass 14 tests
   poetry run pytest -v
   # Expected: 14 passed in ~1-2 seconds
   ```

3. **Configuration Testing:**
   ```bash
   # Test with configured calendar (requires internet access)
   curl -s http://127.0.0.1:8000/cal/testcal
   
   # Test iframe endpoint
   curl -s http://127.0.0.1:8000/iframe/testcal
   ```

## Project Structure & Key Files

### Core Application Files
- `main.py` - FastAPI application with three endpoints: /, /cal/{name}, /iframe/{name}
- `config.ini` - Required configuration file (create manually)
- `pyproject.toml` - Poetry dependency management 
- `poetry.lock` - Locked dependency versions

### Templates
- `templates/iframe.html` - Main calendar display template with dark/light mode support
- `templates/error.html` - Error page template

### Testing
- `tests/test_main.py` - Comprehensive test suite covering all endpoints and functionality
- `pytest.ini` - Pytest configuration

### Build & CI
- `Dockerfile` - Multi-stage Docker build (DOES NOT WORK due to network restrictions)
- `.github/workflows/test.yml` - CI pipeline for testing
- `.github/workflows/docker-image.yml` - Docker image build pipeline

## API Endpoints

1. **Root (`/`)** - Returns HTTP 418 "I am a teapot" status
2. **Calendar Data (`/cal/{name}`)** - Returns JSON calendar events
3. **Calendar Display (`/iframe/{name}`)** - Returns HTML iframe for calendar display

Parameters: `timezone`, `days`, `locale`, `width`, `colour`

## Known Limitations

### Docker Build Issues
**DO NOT attempt to build Docker image** - it fails due to network restrictions when trying to install Poetry from install.python-poetry.org. The error occurs during the Poetry installation step.

### Network Dependencies
- Application requires internet access to fetch external .ics files
- In restricted environments, external calendar URLs will fail with DNS resolution errors
- Use local .ics files for testing when internet access is limited

### No Linting Tools
- Project does not include linting tools like flake8, black, or mypy
- Only pytest is available for code validation
- Code style is maintained manually

## Development Workflow

### Making Changes
1. **ALWAYS run tests first:** `poetry run pytest -v`
2. Make your changes to the codebase
3. **ALWAYS run tests after changes:** `poetry run pytest -v` 
4. Start the application and verify functionality manually
5. Test relevant endpoints with curl commands

### Dependency Management
```bash
# Add new dependency
poetry add package_name

# Add development dependency  
poetry add --group dev package_name

# Update dependencies
poetry update
```

### Common Tasks

#### Adding New Calendar Configuration
Edit `config.ini` and add a new section:
```ini
[mycalendar]
url = https://example.com/mycalendar.ics
timezone = UTC
locale = en_US
```

#### Testing New Calendar Sources
```bash
# Test the calendar data endpoint
curl -s http://127.0.0.1:8000/cal/mycalendar

# Test the iframe display
curl -s http://127.0.0.1:8000/iframe/mycalendar
```

#### Debugging Template Issues
- Check `templates/iframe.html` for HTML structure
- Check `templates/error.html` for error display formatting
- Templates use Jinja2 syntax with responsive dark/light mode CSS

## Time Expectations & Timeouts

- **Poetry install:** ~5 seconds, set timeout to 60+ seconds, NEVER CANCEL
- **Test execution:** ~2 seconds, set timeout to 30+ seconds, NEVER CANCEL  
- **Application startup:** ~1-2 seconds
- **Docker build:** DOES NOT WORK - fails due to network restrictions

## Troubleshooting

### Application Won't Start
- Ensure `config.ini` exists in project root
- Check Poetry virtual environment: `poetry env info`
- Verify dependencies: `poetry show`

### Tests Failing
- Check Python version: `python3 --version` (requires 3.12+)
- Reinstall dependencies: `poetry install --no-interaction`
- Check test configuration in `pytest.ini`

### Calendar Data Not Loading
- Verify internet connectivity for external URLs
- Check `config.ini` syntax and URL format
- Test with a known working .ics URL

### Template Rendering Issues
- Check Jinja2 template syntax in `templates/` directory
- Verify CSS and JavaScript in iframe.html
- Test with different browsers for compatibility

## Key Implementation Details

- **FastAPI:** ASGI web framework with automatic API documentation
- **Uvicorn:** ASGI server for development and production
- **icalevents:** Library for parsing iCal/ICS files  
- **Jinja2:** Template engine for HTML rendering
- **Babel:** Internationalization and localization
- **Pytest:** Testing framework with async support

Always reference these instructions first before exploring the codebase or running commands.