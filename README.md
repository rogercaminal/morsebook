# MorseBook

A local Project Gutenberg Morse-code audiobook player.

- Python/FastAPI backend
- SQLite persistence
- Server-side Gutenberg TXT cache
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

## Run without Docker

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

## Example Gutenberg IDs

- `11`: Alice's Adventures in Wonderland
- `1342`: Pride and Prejudice
- `84`: Frankenstein

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
