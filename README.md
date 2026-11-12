# SPECTRA

**Submission Pre-Bind Evidence Check and Truth Review Agent** — a Python API and React dashboard for reviewing insurance submissions against their extracted JSON and supporting documents.

## What's included

- `code/` — Python review API, agent workflow, React UI, rules, and setup files.
- `documents/` — four account submission folders with source documents and extracted data.
- `code/data/extracted_models/` — saved extraction snapshots.
- SQLite creates `code/data/spectra.db` locally the first time the app runs. Run history is not bundled; run the reviews in the target environment.

## Prerequisites

- Python 3.11 or newer
- An OpenAI API key for GPT-assisted reviews

## Clone and run (Windows PowerShell)

```powershell
git clone --branch feature/spectra-project-setup https://github.com/jaluDadhich/spectra.git
cd spectra
cd code
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Put your key in `code\.env` as `OPENAI_API_KEY=...`. `.env` is ignored by Git; never commit it. The app creates a new local SQLite database. Submission files for all four accounts are under `documents/` and are ready to run.

## Clone and run (macOS / Linux)

```sh
git clone --branch feature/spectra-project-setup https://github.com/jaluDadhich/spectra.git
cd spectra
cd code
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Add `OPENAI_API_KEY=...` to `code/.env`. Keep that file private.

## Start the app

From `code/` with the virtual environment active:

```sh
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/>. The API docs are at <http://127.0.0.1:8765/docs>.

Set `SPECTRA_LLM_ENABLED=true` in `code/.env` to enable GPT-assisted reviews. The committed `.env.example` provides the supported model and budget defaults. Never commit `code/.env` or other credentials.
