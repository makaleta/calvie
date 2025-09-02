# Calvie

**Calvie** is a simple iCal viewer built with FastAPI.

## Features

- View iCal events
- Simple and easy to use interface

## Requirements

- Python 3.12+
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

2. Install dependencies using Poetry:
    ```sh
    poetry install
    ```
3. Configure using 'config.ini' file.
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
    poetry run uvicorn main:app --reload
    ```

2. Open your browser and navigate to `http://127.0.0.1:8000`.

## Docker

You can also run the application using Docker:

1. Build the Docker image:
    ```sh
    docker build -t calvie .
    ```

2. Run the Docker container:
    ```sh
    docker run -p 8080:8080 calvie
    ```

## Testing

The project includes comprehensive unit tests. To run the tests:

```sh
poetry run pytest
```

Or with verbose output:
```sh
poetry run pytest -v
```

## License

This project is licensed under the MIT License.