# MorseBook

A local Project Gutenberg Morse-code audiobook player.

- Python/FastAPI backend
- Bootstrap responsive web UI with day/night mode
- SQLite persistence
- Server-side Gutenberg TXT cache
- Project Gutenberg title lookup and external book links
- Gutenberg header/footer stripping
- Chapter and segment navigation
- jscwlib playback in the browser
- Per-segment CW settings
- Built-in and custom CW profiles
- Resume/bookmark support
- Approximate character timing metadata for better mid-segment resume

## Run with Docker

```bash
docker build -t morsebook .
docker run --rm -p 8000:8000 -v morsebook_data:/app/data morsebook
```

Open <http://localhost:8000>.

## Run with Docker Compose

Standalone:

```bash
docker compose up -d --build
```

Open <http://localhost:8000>.

The compose file stores SQLite data and cached Gutenberg TXT files in the named volume `morsebook_data`.

To copy this service into another Docker Compose file, include:

```yaml
services:
  morsebook:
    build:
      context: /path/to/morsebook
    container_name: morsebook
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - morsebook_data:/app/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=5).read()"
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

volumes:
  morsebook_data:
```

If port `8000` is already used on the Raspberry Pi, change the left side only, for example `"8010:8000"`.

## Run without Docker

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

## Importing books

Use a Project Gutenberg ID, then optionally click **Lookup title** to confirm the book before importing.

Import options:

- `Target segment seconds`: desired listening duration for each generated chunk.
- `Split WPM`: WPM used to estimate how many characters fit in the target duration.
- `Refresh cache`: downloads the TXT again instead of using `data/raw/`.

The segment size target is approximately:

```text
target_segment_seconds * split_wpm * 5 / 60
```

The splitter treats this as a soft target. It may merge short headings or tiny wrap remainders with neighboring text so that very small fragments do not become standalone chunks. Changing playback WPM later does not resplit a book; click **Import / Rebuild** again after changing import options.

## Example Gutenberg IDs

- `11`: Alice's Adventures in Wonderland
- `1342`: Pride and Prejudice
- `84`: Frankenstein

## Tests

Install dependencies, then run pytest:

```bash
. .venv/bin/activate
python -m pytest -v
```

The tests cover segment sizing and the short-fragment merging behavior used during import.

## Data model

SQLite tables:

- `books`
- `chapters`
- `segments`
- `progress`
- `segment_params`
- `cw_profiles`

Raw Gutenberg TXT files are cached in `data/raw/`.

## Notes on timing metadata

The backend estimates Morse timing using PARIS-style dot timing and the current WPM/effective WPM. jscwlib is still the real audio engine; metadata is used for bookmark/resume approximation.
