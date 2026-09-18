# Calvie

**Calvie** is a simple iCal viewer built with FastAPI.

## Features

- View iCal events
- Simple and easy to use interface

## Requirements

- Python 3.14+
- FastAPI
- Uvicorn
- icalevents
- Jinja2
- Babel

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/makaleta/calvie.git
    cd calvie
    ```

2. Install dependencies using [uv](https://docs.astral.sh/uv/getting-started/installation/):
    ```sh
    uv sync --locked
    ```
3. For named calendars, create `config.ini` in the repository root. Direct calendar URLs work without it.
Example contents:
```aiignore
[DEFAULT]
timezone = Europe/London
days to future = 40
locale = en_GB
width = 355

[exampleCal]
url = https://example.com/calendar.ics
```

## Usage

1. Run the FastAPI application:
    ```sh
    uv run --locked uvicorn main:app --reload
    ```

2. Open your browser and navigate to `http://127.0.0.1:8000`.

### Iframe color scheme

Use `color_scheme` on `/iframe/{name}` to select a theme:

- `light` or `dark`: select one theme.
- `light dark` or `dark light`: follow the browser's color preference.
- `normal`: use the default light styling without automatic dark colors.

For example: `/iframe/exampleCal?color_scheme=light%20dark`.
Without a theme parameter, the iframe follows the browser's preference.
The legacy `colour=white` and `colour=black` parameters remain supported;
`color_scheme` takes precedence when both parameters are supplied.

## Docker

You can also run the application using Docker:

1. Build the Docker image:
    ```sh
    docker build -t calvie .
    ```

2. Run the Docker container with your calendar configuration:
    ```sh
    docker run --rm -p 8080:8080 -v "$PWD/config.ini:/app/config.ini:ro" calvie
    ```

## Testing

The project includes comprehensive unit tests. To run the tests:

```sh
uv run --locked pytest
```

Or with verbose output:
```sh
uv run --locked pytest -v
```

Dependencies are locked in `uv.lock`. After changing dependencies, run `uv lock`
and commit both `pyproject.toml` and `uv.lock`. To update locked versions within
the declared ranges, run `uv lock --upgrade` and rerun the tests.

## License

This project is licensed under the MIT License.